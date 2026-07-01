"""Agentic test generation from Jira acceptance criteria.

Instead of routing an AC onto a fixed catalog of hand-written checks, the agent
**generates the actual verification** for each acceptance-criterion: it inspects
the relevant BigQuery mart schema, asks Gemini to produce a concrete read-only
SQL check, then validates that check with a dry-run before it is embedded into
the generated ``.feature`` file and opened as a PR.

Trust model: the generated SQL is human-reviewed in the PR and only runs in CI
after approval + merge. Generation is best-effort — an AC that can't be turned
into a valid, dry-runnable check is returned as ``None`` (skipped) rather than
emitting a wrong test.

A check spec is a dict:
    {
      "kind": "bq_query",
      "table": "customer_enriched",
      "sql":   "SELECT COUNTIF(...) AS violations FROM `proj.ds.customer_enriched`",
      "assert": {"column": "violations", "equals": 0},
      "rationale": "<why this verifies the AC>"
    }
"""

from __future__ import annotations

import json
import logging
import re
import time

from agent.config import SETTINGS
from agent.tools import bigquery_toolset as bq
from agent.tools import neo4j_toolset as neo

logger = logging.getLogger(__name__)

# The data marts the agent can write checks against.
KNOWN_MARTS = ("customer_enriched", "account_enriched", "address_enriched")

# AC keywords that mean "verify the Neo4j graph" -> generate a Cypher check
# instead of a BigQuery one.
GRAPH_MARKERS = ("neo4j", "graph", "node", "nodes", "relationship",
                 "relationships", "cypher")

# AC keywords that mean "BigQuery side" — used to detect a *cross-system* AC
# (graph AND BigQuery), which can't be one query and becomes a cross_check.
BQ_MARKERS = ("bigquery", "mart", *KNOWN_MARTS)

# Generation attempts and retry delay for transient Vertex errors. Multiple ACs
# are generated back-to-back, so the 3rd+ call can hit a per-minute 429; we
# retry up to 3 times, 5 seconds apart, rather than dropping the AC to @manual.
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 5
_TRANSIENT_MARKERS = ("429", "resource_exhausted", "rate limit", "quota",
                      "503", "unavailable", "try again")


def _is_transient(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


def _is_graph_ac(ac_text: str) -> bool:
    """True when the AC is about the Neo4j graph (so we generate Cypher)."""
    text = ac_text.lower()
    return any(m in text for m in GRAPH_MARKERS)


def _is_cross_system_ac(ac_text: str) -> bool:
    """True when the AC compares the Neo4j graph against BigQuery.

    These can't be a single query, so we generate a cross_check: capture a value
    from each system and compare them.
    """
    text = ac_text.lower()
    return (any(m in text for m in GRAPH_MARKERS)
            and any(m in text for m in BQ_MARKERS))


def _candidate_tables(ac_text: str) -> list[str]:
    """Pick the most likely mart(s) referenced by the AC, falling back to all."""
    text = ac_text.lower()
    hits = [t for t in KNOWN_MARTS if t in text]
    if hits:
        return hits
    # entity hints without the _enriched suffix
    for entity, mart in (("customer", "customer_enriched"),
                         ("account", "account_enriched"),
                         ("address", "address_enriched")):
        if re.search(rf"\b{entity}\b", text):
            hits.append(mart)
    return hits or list(KNOWN_MARTS)


def _schema_context(tables: list[str]) -> str:
    """Render the column list of each candidate table for the prompt."""
    lines: list[str] = []
    for t in tables:
        info = bq.table_schema(t)
        if "error" in info or not info.get("columns"):
            continue
        cols = ", ".join(f"{c['name']} {c['type']}" for c in info["columns"])
        lines.append(f"- `{info['table']}` ({t}): {cols}")
    return "\n".join(lines)


def _graph_context() -> str:
    """Render the Neo4j node labels + relationship types for the prompt."""
    s = neo.graph_schema()
    labels = s.get("labels") or []
    rels = s.get("relationships") or []
    if not labels and not rels:
        return ""
    return (f"Node labels: {', '.join(labels) or '(none)'}\n"
            f"Relationship types: {', '.join(rels) or '(none)'}")


_BQ_PROMPT = """\
You are a data QA engineer. Convert ONE Jira acceptance criterion into a single
read-only BigQuery check.

Acceptance criterion:
{ac}

Available tables and columns (use the fully-qualified backticked name exactly):
{schema}

Rules:
- Return ONLY JSON, no prose, matching exactly:
  {{"kind":"bq_query","table":"<mart>","sql":"<one SELECT/WITH statement>",
    "assert":{{"column":"<name>","equals":0}},"rationale":"<short why>"}}
- The SQL MUST be a single SELECT/WITH statement (no DML/DDL, no ';').
- The SQL MUST return exactly one row containing the assert column.
- Design the check so the assert column equals 0 when the criterion HOLDS
  (e.g. COUNT/COUNTIF of violating rows), so equals is almost always 0.
- Only reference columns that exist in the tables above.
- Prefer COUNTIF(<violation condition>) AS violations.
"""

_CYPHER_PROMPT = """\
You are a graph QA engineer. Convert ONE Jira acceptance criterion into a single
read-only Cypher check against Neo4j.

Acceptance criterion:
{ac}

Graph model:
{schema}

Rules:
- Return ONLY JSON, no prose, matching exactly:
  {{"kind":"cypher","cypher":"<one read-only MATCH ... RETURN query>",
    "assert":{{"column":"<name>","equals":0}},"rationale":"<short why>"}}
- The Cypher MUST be read-only (no CREATE/MERGE/DELETE/SET/REMOVE/DROP/DETACH).
- It MUST return exactly one row containing the assert column.
- Design the check so the assert column equals 0 when the criterion HOLDS
  (e.g. count of orphan/violating nodes), so equals is almost always 0.
- Only use the node labels and relationship types listed above.
- Prefer count(<violating pattern>) AS violations.
"""

_CROSS_PROMPT = """\
You are a data QA engineer. The acceptance criterion compares a value in the
Neo4j graph against a value in BigQuery, so produce TWO read-only queries — one
per system — that each return a single scalar named `value`, to be compared.

Acceptance criterion:
{ac}

BigQuery tables and columns (use the fully-qualified backticked name exactly):
{bq_schema}

Neo4j graph model:
{graph_schema}

Rules:
- Return ONLY JSON, no prose, matching exactly:
  {{"kind":"cross_check","bq_sql":"<one SELECT/WITH returning AS value>",
    "cypher":"<one read-only MATCH ... RETURN ... AS value>",
    "compare":"eq","rationale":"<short why>"}}
- bq_sql MUST be a single SELECT/WITH (no DML/DDL, no ';') returning one row
  with a column named exactly `value`.
- cypher MUST be read-only (no CREATE/MERGE/DELETE/SET/REMOVE/DROP/DETACH)
  returning one row with a column named exactly `value`.
- Both `value`s should be equal when the criterion HOLDS; "compare" is "eq".
- Only reference real columns / labels / relationship types listed above.
"""


def _llm_generate(prompt: str) -> str:
    """Call Gemini and return raw text. Lazily imported so the module loads
    even where google-genai isn't installed (overridden in tests)."""
    from google import genai  # noqa: PLC0415

    client = genai.Client()
    resp = client.models.generate_content(
        model=SETTINGS.model,
        contents=prompt,
        config={"response_mime_type": "application/json", "temperature": 0},
    )
    _record_usage(resp)
    return resp.text or ""


def _record_usage(resp: object) -> None:
    """Attribute this Gemini call's token usage to the current run (best-effort).

    ``test_generator`` calls Gemini directly (outside the ADK agent loop), so it
    reports its own usage here — otherwise the authoring flow, which is the bulk
    of the token spend, would go untracked. Never raises.
    """
    try:
        from agent.usage import TRACKER

        usage = getattr(resp, "usage_metadata", None)
        TRACKER.record(
            model=SETTINGS.model,
            source="bdd_authoring.test_generator",
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            total_tokens=getattr(usage, "total_token_count", None),
        )
    except Exception:  # noqa: BLE001  # tracking must never break generation
        pass


def _parse_spec(raw: str) -> dict | None:
    """Parse the model's JSON (tolerating ```json fences)."""
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _valid_assert(spec: dict) -> bool:
    a = spec.get("assert")
    return isinstance(a, dict) and isinstance(a.get("column"), str) and "equals" in a


def _valid_bq(spec: dict) -> bool:
    return (isinstance(spec, dict) and spec.get("kind") == "bq_query"
            and isinstance(spec.get("sql"), str) and _valid_assert(spec))


def _valid_cypher(spec: dict) -> bool:
    return (isinstance(spec, dict) and spec.get("kind") == "cypher"
            and isinstance(spec.get("cypher"), str) and _valid_assert(spec))


def _valid_cross(spec: dict) -> bool:
    return (isinstance(spec, dict) and spec.get("kind") == "cross_check"
            and isinstance(spec.get("bq_sql"), str)
            and isinstance(spec.get("cypher"), str))


def generate_check(ticket: str, ac_bullet: str) -> dict | None:
    """Generate + validate a read-only check for one AC bullet.

    Picks the check kind from the AC: ``cross_check`` (graph vs BigQuery),
    ``cypher`` (graph only), or ``bq_query`` (data only). Returns the validated
    spec, or ``None`` when no valid check could be produced (so the caller emits
    an @manual scenario instead of a wrong test).
    """
    if _is_cross_system_ac(ac_bullet):
        # Compares Neo4j against BigQuery -> two queries + a comparison.
        kind = "cross_check"
        bq_schema = _schema_context(_candidate_tables(ac_bullet))
        graph_schema = _graph_context()
        if not bq_schema or not graph_schema:
            logger.warning("generate_check: ticket=%s missing bq/graph schema for cross_check — skipping", ticket)
            return None
        prompt = _CROSS_PROMPT.format(ac=ac_bullet.strip(),
                                      bq_schema=bq_schema, graph_schema=graph_schema)
        valid = _valid_cross
        def validate(spec: dict) -> dict:
            d = bq.dry_run_query(spec["bq_sql"])
            return d if not d.get("ok") else neo.explain(spec["cypher"])
    elif _is_graph_ac(ac_bullet):
        kind = "cypher"
        schema = _graph_context()
        if not schema:
            logger.warning("generate_check: ticket=%s no graph context — skipping", ticket)
            return None
        prompt = _CYPHER_PROMPT.format(ac=ac_bullet.strip(), schema=schema)
        valid = _valid_cypher
        def validate(spec: dict) -> dict:
            return neo.explain(spec["cypher"])
    else:
        kind = "bq_query"
        schema = _schema_context(_candidate_tables(ac_bullet))
        if not schema:
            logger.warning("generate_check: ticket=%s no table schema — skipping", ticket)
            return None
        prompt = _BQ_PROMPT.format(ac=ac_bullet.strip(), schema=schema)
        valid = _valid_bq
        def validate(spec: dict) -> dict:
            return bq.dry_run_query(spec["sql"])

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            raw = _llm_generate(prompt)
        except Exception as exc:  # noqa: BLE001
            transient = _is_transient(exc)
            logger.warning("generate_check: ticket=%s LLM error (attempt %d/%d, transient=%s): %s",
                           ticket, attempt, _MAX_ATTEMPTS, transient, exc)
            # Retry transient throttles (e.g. 429) with a fixed delay; give up
            # on permanent errors or once attempts are exhausted.
            if transient and attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECONDS)
                continue
            return None
        spec = _parse_spec(raw)
        if not spec or not valid(spec):
            logger.warning("generate_check: ticket=%s invalid %s spec (attempt %d/%d): %s",
                           ticket, kind, attempt, _MAX_ATTEMPTS, raw[:200])
            continue
        # Validate the query is genuinely runnable + read-only before trusting it.
        check = validate(spec)
        if not check.get("ok"):
            logger.warning("generate_check: ticket=%s %s validation failed (attempt %d/%d): %s",
                           ticket, kind, attempt, _MAX_ATTEMPTS, check.get("error"))
            continue
        logger.info("generate_check: ticket=%s produced %s check (%s)",
                    ticket, kind, spec.get("table") or kind)
        return spec

    logger.warning("generate_check: ticket=%s could not generate a valid check — skipping", ticket)
    return None
