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

logger = logging.getLogger(__name__)

# The data marts the agent can write checks against.
KNOWN_MARTS = ("customer_enriched", "account_enriched", "address_enriched")

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


_PROMPT = """\
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
    return resp.text or ""


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


def _valid_shape(spec: dict) -> bool:
    return (
        isinstance(spec, dict)
        and spec.get("kind") == "bq_query"
        and isinstance(spec.get("sql"), str)
        and isinstance(spec.get("assert"), dict)
        and isinstance(spec["assert"].get("column"), str)
        and "equals" in spec["assert"]
    )


def generate_check(ticket: str, ac_bullet: str) -> dict | None:
    """Generate + validate a read-only BigQuery check for one AC bullet.

    Returns the check spec, or ``None`` when no valid check could be produced
    (so the caller skips it rather than emitting a wrong test).
    """
    tables = _candidate_tables(ac_bullet)
    schema = _schema_context(tables)
    if not schema:
        logger.warning("generate_check: ticket=%s no schema for tables=%s — skipping", ticket, tables)
        return None
    prompt = _PROMPT.format(ac=ac_bullet.strip(), schema=schema)

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
        if not spec or not _valid_shape(spec):
            logger.warning("generate_check: ticket=%s invalid spec (attempt %d/%d): %s",
                           ticket, attempt, _MAX_ATTEMPTS, raw[:200])
            continue
        # Validate the SQL is genuinely runnable + read-only + bounded.
        dry = bq.dry_run_query(spec["sql"])
        if not dry.get("ok"):
            logger.warning("generate_check: ticket=%s dry-run failed (attempt %d/%d): %s",
                           ticket, attempt, _MAX_ATTEMPTS, dry.get("error"))
            continue
        logger.info("generate_check: ticket=%s produced check on %s (bytes=%s)",
                    ticket, spec.get("table"), dry.get("bytes"))
        return spec

    logger.warning("generate_check: ticket=%s could not generate a valid check — skipping", ticket)
    return None
