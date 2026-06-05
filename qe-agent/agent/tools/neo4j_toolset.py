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
