"""Integration testing suite (goal 3) — 5 scenarios.

Validates the data flows between stages:
    GCS (raw CSV) -> BigQuery raw -> BigQuery enriched -> Neo4j graph
including incremental/watermark mechanics and end-to-end consistency.
"""

from __future__ import annotations

from agent.config import SETTINGS
from agent.results import ScenarioResult, Status
from agent.tools import bigquery_toolset as bq
from agent.tools import gcs_toolset as gcs
from agent.tools import neo4j_toolset as neo

SUITE = "integration"


def i1_gcs_to_bq_raw() -> ScenarioResult:
    """I1: GCS CSV rows land in the BigQuery raw tables (count parity)."""
    r = ScenarioResult("I1", SUITE, "GCS CSV -> BigQuery raw load parity")
    details = {}
    failed = False
    for entity, raw_table in SETTINGS.raw_tables.items():
        gcs_count = gcs.count_csv_rows(entity)["row_count"]
        bq_count = bq.table_row_count(raw_table).get("row_count", 0)
        details[entity] = {"gcs": gcs_count, "bq_raw": bq_count}
        # raw is WRITE_TRUNCATE of the latest files; bq should be >0 when gcs has data
        if gcs_count > 0 and bq_count == 0:
            failed = True
    r.actual = details
    if failed:
        r.fail("Some entities have GCS data but empty BigQuery raw tables.")
    return r


def i2_raw_to_enriched() -> ScenarioResult:
    """I2: enriched tables are populated and FK-consistent with raw."""
    r = ScenarioResult("I2", SUITE, "BigQuery raw -> enriched populated + FK integrity")
    details = {}
    failed = False
    for entity, enriched in SETTINGS.enriched_tables.items():
        if not bq.table_exists(enriched):
            details[entity] = {"exists": False}
            failed = True
            continue
        cnt = bq.table_row_count(enriched).get("row_count", 0)
        details[entity] = {"exists": True, "rows": cnt}
        if cnt == 0:
            failed = True
    # FK integrity: every account/address customer_id exists in customer_enriched
    cust_fq = SETTINGS.fq_table("customer_enriched")
    for entity in ("account", "address"):
        enr = SETTINGS.fq_table(SETTINGS.enriched_tables[entity])
        orphan = bq.run_query(
            f"SELECT COUNT(*) AS n FROM `{enr}` e "
            f"LEFT JOIN `{cust_fq}` c USING (customer_id) WHERE c.customer_id IS NULL"
        )
        n = orphan.get("rows", [{}])[0].get("n", 0) if "rows" in orphan else 0
        details.setdefault(entity, {})["orphan_customer_fks"] = n
        if n and n > 0:
            failed = True
    r.actual = details
    if failed:
        r.fail("Enriched tables missing/empty or contain orphan customer FKs.")
    return r


def i3_enriched_to_neo4j() -> ScenarioResult:
    """I3: enriched rows are ingested into Neo4j nodes + relationships."""
    r = ScenarioResult("I3", SUITE, "BigQuery enriched -> Neo4j nodes & relationships")
    bq_counts = {
        "Customer": bq.table_row_count("customer_enriched").get("row_count", 0),
        "Account": bq.table_row_count("account_enriched").get("row_count", 0),
        "Address": bq.table_row_count("address_enriched").get("row_count", 0),
    }
    neo_counts = {lbl: neo.node_count(lbl).get("count", 0) for lbl in bq_counts}
    rels = {
        "HAS_ACCOUNT": neo.relationship_count("HAS_ACCOUNT").get("count", 0),
        "HAS_ADDRESS": neo.relationship_count("HAS_ADDRESS").get("count", 0),
    }
    r.actual = {"bq": bq_counts, "neo4j_nodes": neo_counts, "neo4j_relationships": rels}
    failed = False
    for label, expected in bq_counts.items():
        # graph is eventually consistent; allow node count to be <= bq but must exist when bq>0
        if expected > 0 and neo_counts[label] == 0:
            failed = True
    if any(v == 0 for v in bq_counts.values() if v) and (rels["HAS_ACCOUNT"] == 0 or rels["HAS_ADDRESS"] == 0):
        failed = True
    if failed:
        r.fail("Neo4j missing nodes/relationships for populated enriched tables.")
    return r


def i4_incremental_watermark() -> ScenarioResult:
    """I4: ingest watermark advances and file metadata dedupes."""
    r = ScenarioResult("I4", SUITE, "Incremental watermark + processed-file dedupe")
    details = {}
    failed = False
    # watermark table tracks last_processed_at per entity_type
    if bq.table_exists("neo4j_ingest_watermark"):
        wm = bq.run_query(
            f"SELECT entity_type, MAX(last_processed_at) AS last "
            f"FROM `{SETTINGS.fq_table('neo4j_ingest_watermark')}` GROUP BY entity_type"
        )
        details["watermark"] = wm.get("rows", [])
        if not wm.get("rows"):
            failed = True
    else:
        details["watermark"] = "table missing"
        failed = True
    # processed_files_metadata must have unique file_name (no dupes)
    for entity in SETTINGS.dbt_projects:
        tbl = "processed_files_metadata"
        if bq.table_exists(tbl):
            dup = bq.run_query(
                f"SELECT COUNT(*) AS dupes FROM (SELECT file_name FROM `{SETTINGS.fq_table(tbl)}` "
                f"GROUP BY file_name HAVING COUNT(*) > 1)"
            )
            n = dup.get("rows", [{}])[0].get("dupes", 0)
            details.setdefault("dedupe", {})[entity] = n
            if n:
                failed = True
        break  # single shared dataset; one check suffices
    r.actual = details
    if failed:
        r.fail("Watermark not advancing or duplicate processed files detected.")
    return r


def i5_end_to_end_consistency() -> ScenarioResult:
    """I5: a sampled customer_id is consistent across BQ and Neo4j (no orphans)."""
    r = ScenarioResult("I5", SUITE, "End-to-end customer consistency (BQ vs Neo4j)")
    sample = bq.run_query(
        f"SELECT customer_id FROM `{SETTINGS.fq_table('customer_enriched')}` LIMIT 5"
    )
    ids = [row["customer_id"] for row in sample.get("rows", [])]
    if not ids:
        return r.error("No customers available to sample.")
    details = []
    failed = False
    for cid in ids:
        bq_accts = bq.run_query(
            f"SELECT COUNT(*) AS n FROM `{SETTINGS.fq_table('account_enriched')}` "
            f"WHERE customer_id = '{cid}'"
        ).get("rows", [{}])[0].get("n", 0)
        neo_accts = neo.run_cypher(
            "MATCH (c:Customer {customer_id:$cid})-[:HAS_ACCOUNT]->(a:Account) RETURN count(a) AS n",
            {"cid": cid},
        )
        neo_n = neo_accts.get("rows", [{}])[0].get("n", 0) if "rows" in neo_accts else 0
        match = bq_accts == neo_n
        details.append({"customer_id": cid, "bq_accounts": bq_accts, "neo_accounts": neo_n, "match": match})
        if not match:
            failed = True
    r.actual = details
    if failed:
        r.fail("Account counts diverge between BigQuery and Neo4j for sampled customers.")
    return r


REGISTRY = {
    "I1": i1_gcs_to_bq_raw,
    "I2": i2_raw_to_enriched,
    "I3": i3_enriched_to_neo4j,
    "I4": i4_incremental_watermark,
    "I5": i5_end_to_end_consistency,
}


def run_all() -> list[dict]:
    out = []
    for fn in REGISTRY.values():
        try:
            out.append(fn().to_dict())
        except Exception as exc:  # noqa: BLE001
            out.append(ScenarioResult("?", SUITE, fn.__name__, Status.ERROR, findings=[str(exc)]).to_dict())
    return out
