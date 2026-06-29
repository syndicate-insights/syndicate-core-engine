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


# Default cap on bytes a generated/ad-hoc query may scan (10 GB). Keeps an
# LLM-authored query from accidentally scanning a huge table.
_MAX_BYTES_BILLED = 10 * 1024**3


def _readonly_reason(sql: str) -> str | None:
    """Return why `sql` is not an allowed read-only single statement, or None."""
    stripped = sql.strip().rstrip(";")
    low = stripped.lstrip().lower()
    if not (low.startswith("select") or low.startswith("with")):
        return "Only SELECT/WITH queries are permitted by the QE agent."
    if ";" in stripped:
        return "Only a single statement is permitted (no ';')."
    return None


def run_query(sql: str, timed: bool = False) -> dict:
    """Run a read-only SQL query and return rows as dicts.

    Guards against accidental DML/DDL — only SELECT/WITH statements are allowed.
    """
    reason = _readonly_reason(sql)
    if reason:
        return {"error": reason}
    start = time.monotonic()
    job = _client().query(
        sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=_MAX_BYTES_BILLED)
    )
    rows = [dict(r) for r in job.result()]
    elapsed = time.monotonic() - start
    out = {"row_count": len(rows), "rows": rows}
    if timed:
        out["elapsed_seconds"] = round(elapsed, 3)
        out["bytes_processed"] = job.total_bytes_processed
    return out


def dry_run_query(sql: str) -> dict:
    """Validate a read-only query without executing it.

    Used at authoring time to confirm an LLM-generated check parses, references
    real tables/columns, and is within the byte cap — before it lands in a PR.
    Returns ``{"ok": True, "bytes": N}`` or ``{"ok": False, "error": ...}``.
    """
    reason = _readonly_reason(sql)
    if reason:
        return {"ok": False, "error": reason}
    try:
        job = _client().query(
            sql,
            job_config=bigquery.QueryJobConfig(
                dry_run=True, use_query_cache=False,
                maximum_bytes_billed=_MAX_BYTES_BILLED,
            ),
        )
        bytes_ = job.total_bytes_processed or 0
        if bytes_ > _MAX_BYTES_BILLED:
            return {"ok": False, "error": f"query would scan {bytes_} bytes (> cap)"}
        return {"ok": True, "bytes": bytes_}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def run_check(sql: str, column: str, equals) -> dict:
    """Execute a read-only check query and assert ``column`` of row 0 == ``equals``.

    Returns a deterministic ``status`` (PASS/FAIL/ERROR) for Harness to gate on,
    plus the actual/expected values for the Jira comment.
    """
    res = run_query(sql)
    if "error" in res:
        return {"status": "ERROR", "expected": equals, "actual": None,
                "findings": [res["error"]], "sql": sql}
    rows = res.get("rows") or []
    if not rows or column not in rows[0]:
        return {"status": "ERROR", "expected": equals, "actual": None,
                "findings": [f"check query returned no '{column}' column"],
                "rows": rows[:5], "sql": sql}
    actual = rows[0][column]
    passed = actual == equals
    return {
        "status": "PASS" if passed else "FAIL",
        "expected": equals,
        "actual": actual,
        "findings": [] if passed else [f"{column}={actual!r}, expected {equals!r}"],
        "sql": sql,
    }


def run_value(sql: str, column: str = "value") -> dict:
    """Execute a read-only query and return the scalar ``column`` of row 0.

    Used by cross-system checks that capture a BigQuery value to compare against
    another system. Returns ``{"status": "OK", "value": N}`` or an ERROR status.
    """
    res = run_query(sql)
    if "error" in res:
        return {"status": "ERROR", "value": None, "findings": [res["error"]], "sql": sql}
    rows = res.get("rows") or []
    if not rows or column not in rows[0]:
        return {"status": "ERROR", "value": None,
                "findings": [f"query returned no '{column}' column"], "rows": rows[:5], "sql": sql}
    return {"status": "OK", "value": rows[0][column], "sql": sql}


def table_row_count(table: str) -> dict:
    """Row count for a table in the configured dataset."""
    fq = SETTINGS.fq_table(table)
    res = run_query(f"SELECT COUNT(*) AS n FROM `{fq}`")  # nosec B608
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
    return run_query(f"SELECT * FROM `{fq}`{clause} LIMIT {int(limit)}")  # nosec B608
