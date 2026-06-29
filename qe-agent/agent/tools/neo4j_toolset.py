"""Neo4j toolset: read-only Cypher queries against the graph (read-only)."""

from __future__ import annotations

from functools import lru_cache

from neo4j import GraphDatabase

from agent.config import SETTINGS

_WRITE_KEYWORDS = ("create", "merge", "delete", "set ", "remove", "drop", "detach")


@lru_cache(maxsize=1)
def _driver():
    return GraphDatabase.driver(
        SETTINGS.neo4j_uri,
        auth=(SETTINGS.neo4j_user, SETTINGS.neo4j_password),
    )


def run_cypher(query: str, params: dict | None = None) -> dict:
    """Run a read-only Cypher query. Write clauses are rejected."""
    lowered = query.lower()
    if any(kw in lowered for kw in _WRITE_KEYWORDS):
        return {"error": "Only read-only Cypher is permitted by the QE agent."}
    with _driver().session() as session:
        result = session.run(query, params or {})
        records = [r.data() for r in result]
    return {"row_count": len(records), "rows": records}


def node_count(label: str) -> dict:
    res = run_cypher(f"MATCH (n:{label}) RETURN count(n) AS n")
    if "error" in res:
        return res
    return {"label": label, "count": res["rows"][0]["n"]}


def relationship_count(rel_type: str) -> dict:
    res = run_cypher(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS n")
    if "error" in res:
        return res
    return {"relationship": rel_type, "count": res["rows"][0]["n"]}


def list_constraints() -> dict:
    return run_cypher("SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties, type RETURN *")


def graph_schema() -> dict:
    """Node labels + relationship types, for grounding generated Cypher checks."""
    labels = run_cypher("CALL db.labels() YIELD label RETURN collect(label) AS labels")
    rels = run_cypher(
        "CALL db.relationshipTypes() YIELD relationshipType "
        "RETURN collect(relationshipType) AS rels"
    )
    return {
        "labels": (labels.get("rows") or [{}])[0].get("labels", []) if "error" not in labels else [],
        "relationships": (rels.get("rows") or [{}])[0].get("rels", []) if "error" not in rels else [],
    }


def explain(query: str) -> dict:
    """Validate a read-only Cypher query without executing it (EXPLAIN).

    Used at authoring time so an LLM-generated check is confirmed parseable +
    read-only before it lands in a PR. Returns {"ok": True} or {"ok": False,...}.
    """
    lowered = query.lower()
    if any(kw in lowered for kw in _WRITE_KEYWORDS):
        return {"ok": False, "error": "Only read-only Cypher is permitted by the QE agent."}
    try:
        with _driver().session() as session:
            session.run(f"EXPLAIN {query}").consume()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def run_check(query: str, column: str, equals) -> dict:
    """Execute a read-only Cypher check and assert ``column`` of row 0 == ``equals``.

    Mirrors bigquery_toolset.run_check; returns a deterministic PASS/FAIL/ERROR
    status for Harness to gate on.
    """
    res = run_cypher(query)
    if "error" in res:
        return {"status": "ERROR", "expected": equals, "actual": None,
                "findings": [res["error"]], "cypher": query}
    rows = res.get("rows") or []
    if not rows or column not in rows[0]:
        return {"status": "ERROR", "expected": equals, "actual": None,
                "findings": [f"check query returned no '{column}' column"],
                "rows": rows[:5], "cypher": query}
    actual = rows[0][column]
    passed = actual == equals
    return {
        "status": "PASS" if passed else "FAIL",
        "expected": equals,
        "actual": actual,
        "findings": [] if passed else [f"{column}={actual!r}, expected {equals!r}"],
        "cypher": query,
    }


def run_value(query: str, column: str = "value") -> dict:
    """Execute a read-only Cypher query and return the scalar ``column`` of row 0.

    Used by cross-system checks that capture a Neo4j value to compare against
    another system. Returns ``{"status": "OK", "value": N}`` or an ERROR status.
    """
    res = run_cypher(query)
    if "error" in res:
        return {"status": "ERROR", "value": None, "findings": [res["error"]], "cypher": query}
    rows = res.get("rows") or []
    if not rows or column not in rows[0]:
        return {"status": "ERROR", "value": None,
                "findings": [f"query returned no '{column}' column"], "rows": rows[:5], "cypher": query}
    return {"status": "OK", "value": rows[0][column], "cypher": query}
