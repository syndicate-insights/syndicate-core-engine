"""BigQuery toolset: read-only queries + dbt-test job triggering.

The agent never mutates pipeline data. The only "write" action is creating a
short-lived Kubernetes Job that runs `dbt test` (see kubernetes_toolset); BQ
access here is strictly SELECT / metadata.
"""

from __future__ import annotations

import time
from functools import lru_cache

from google.cloud import bigquery

from agent.config import SETTINGS
from agent.tools.credentials import get_credentials


@lru_cache(maxsize=1)
def _client() -> bigquery.Client:
    return bigquery.Client(
        project=SETTINGS.gcp_project,
        credentials=get_credentials(),
        location=SETTINGS.bq_location,
    )


def run_query(sql: str, timed: bool = False) -> dict:
    """Run a read-only SQL query and return rows as dicts.

    Guards against accidental DML/DDL — only SELECT/WITH statements are allowed.
    """
    stripped = sql.lstrip().lower()
    if not (stripped.startswith("select") or stripped.startswith("with")):
        return {"error": "Only SELECT/WITH queries are permitted by the QE agent."}
    start = time.monotonic()
    job = _client().query(sql)
    rows = [dict(r) for r in job.result()]
    elapsed = time.monotonic() - start
    out = {"row_count": len(rows), "rows": rows}
    if timed:
        out["elapsed_seconds"] = round(elapsed, 3)
        out["bytes_processed"] = job.total_bytes_processed
    return out


def table_row_count(table: str) -> dict:
    """Row count for a table in the configured dataset."""
    fq = SETTINGS.fq_table(table)
    res = run_query(f"SELECT COUNT(*) AS n FROM `{fq}`")
    if "error" in res:
        return res
    return {"table": fq, "row_count": res["rows"][0]["n"]}


def table_schema(table: str) -> dict:
    """Column names + types for a table."""
    fq = SETTINGS.fq_table(table)
    tbl = _client().get_table(fq)
    return {
        "table": fq,
        "columns": [{"name": f.name, "type": f.field_type, "mode": f.mode} for f in tbl.schema],
        "num_rows": tbl.num_rows,
    }


def table_exists(table: str) -> bool:
    try:
        _client().get_table(SETTINGS.fq_table(table))
        return True
    except Exception:
        return False


def sample_rows(table: str, limit: int = 20, where: str = "") -> dict:
    fq = SETTINGS.fq_table(table)
    clause = f" WHERE {where}" if where else ""
    return run_query(f"SELECT * FROM `{fq}`{clause} LIMIT {int(limit)}")
