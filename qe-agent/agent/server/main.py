"""FastAPI server for the QE Quality Agent.

Two surfaces share one process:
  - ADK agent surface (`/run`, `/run_sse`, session APIs) via `get_fast_api_app`,
    used for LLM-driven triage / interactive quality sweeps.
  - Deterministic QE surface (`/qe/...`) that Harness CI calls to run a suite or
    a single scenario and gate purely on the JSON `status` field.
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI, HTTPException, Request

from agent.observability import configure_logging
from agent.scenarios import runner
from agent.server import jira_webhook
from agent.sub_agents.bdd_authoring.agent import (
    author_bdd_scenarios,
    harness_latest_bdd,
    jira_read_acceptance_criteria,
    jira_sync_results,
    run_and_sync_scenario,
    update_bdd_from_failure,
)

configure_logging()
logger = logging.getLogger(__name__)

AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_adk_app() -> FastAPI | None:
    """Best-effort construction of the ADK FastAPI app.

    Kept optional so the deterministic QE endpoints still serve even if the ADK
    web assets / model backend are unavailable (e.g. in a minimal CI image).
    """
    try:
        from google.adk.cli.fast_api import get_fast_api_app

        adk_app = get_fast_api_app(agents_dir=AGENTS_DIR, web=True)
        logger.info("ADK app initialised from agents_dir=%s", AGENTS_DIR)
        return adk_app
    except Exception as exc:  # noqa: BLE001
        logger.warning("ADK app unavailable, falling back to plain FastAPI: %s", exc)
        return None


app: FastAPI = _build_adk_app() or FastAPI(title="QE Quality Agent")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/qe/scenarios")
def list_scenarios() -> dict:
    """List all available suites and their scenario ids."""
    logger.debug("list_scenarios called")
    result = runner.list_scenarios()
    logger.debug("list_scenarios result: %s", result)
    return result


@app.get("/qe/suite/{suite}")
def run_suite(suite: str) -> dict:
    """Run every scenario in a suite. `passed` is the deterministic gate."""
    logger.info("run_suite called suite=%s", suite)
    result = runner.run_suite(suite)
    logger.info("run_suite suite=%s passed=%s", suite, result.get("passed"))
    return result


@app.get("/qe/scenario/{suite}/{scenario_id}")
def run_scenario(suite: str, scenario_id: str) -> dict:
    """Run a single scenario by id."""
    logger.info("run_scenario called suite=%s scenario_id=%s", suite, scenario_id)
    result = runner.run_scenario(suite, scenario_id)
    logger.info("run_scenario suite=%s scenario_id=%s status=%s", suite, scenario_id, result.get("status"))
    return result


@app.post("/qe/scenario/{suite}/{scenario_id}/run-sync")
def run_scenario_and_sync(suite: str, scenario_id: str, ticket: str = "",
                          execution_url: str | None = None) -> dict:
    """Run a non-BDD check and sync its PASS/FAIL to the matching Jira subtask.

    Called by the Harness CodingStandards / StaticAnalysis / NonFunctional
    stages (native HTTP step — no curl, no Cucumber). The top-level ``status``
    field is the deterministic gate. ``ticket`` is optional: when omitted the
    check still runs and gates, it just isn't synced to Jira.
    """
    logger.info("run_scenario_and_sync suite=%s scenario_id=%s ticket=%s", suite, scenario_id, ticket)
    out = run_and_sync_scenario(suite, scenario_id, ticket=ticket, execution_url=execution_url)
    logger.info("run_scenario_and_sync suite=%s scenario_id=%s status=%s synced=%s",
                suite, scenario_id, out.get("status"), bool((out.get("sync") or {}).get("updated")))
    return out


@app.post("/qe/query/check")
async def query_check(request: Request) -> dict:
    """Execute an agent-generated, read-only BigQuery check and assert a result.

    The BDD pack's generated scenarios POST the embedded SQL here. Body:
        {"sql": "SELECT COUNTIF(...) AS violations FROM `...`",
         "column": "violations", "equals": 0}
    Returns a deterministic ``status`` (PASS/FAIL/ERROR) plus actual/expected,
    which the Cucumber step gates on. Read-only is enforced in run_query.
    """
    from agent.tools import bigquery_toolset as bq

    body = await request.json()
    sql = (body or {}).get("sql")
    column = (body or {}).get("column", "violations")
    equals = (body or {}).get("equals", 0)
    if not sql or not isinstance(sql, str):
        raise HTTPException(status_code=400, detail="missing 'sql' in request body")
    logger.info("query_check: column=%s equals=%r sql=%s", column, equals, sql[:200])
    result = bq.run_check(sql, column, equals)
    logger.info("query_check: status=%s actual=%r", result.get("status"), result.get("actual"))
    return result


@app.post("/qe/cypher/check")
async def cypher_check(request: Request) -> dict:
    """Execute an agent-generated, read-only Cypher check and assert a result.

    The BDD pack's generated Neo4j scenarios POST the embedded Cypher here. Body:
        {"cypher": "MATCH (a:Account) WHERE NOT (:Customer)-[:HAS_ACCOUNT]->(a)
                    RETURN count(a) AS violations",
         "column": "violations", "equals": 0}
    Returns a deterministic PASS/FAIL/ERROR. Read-only is enforced in run_cypher.
    """
    from agent.tools import neo4j_toolset as neo

    body = await request.json()
    cypher = (body or {}).get("cypher")
    column = (body or {}).get("column", "violations")
    equals = (body or {}).get("equals", 0)
    if not cypher or not isinstance(cypher, str):
        raise HTTPException(status_code=400, detail="missing 'cypher' in request body")
    logger.info("cypher_check: column=%s equals=%r cypher=%s", column, equals, cypher[:200])
    result = neo.run_check(cypher, column, equals)
    logger.info("cypher_check: status=%s actual=%r", result.get("status"), result.get("actual"))
    return result


@app.post("/qe/query/value")
async def query_value(request: Request) -> dict:
    """Run a read-only BigQuery query and return the scalar ``column`` (default
    ``value``). Used by cross-system checks to capture a value for comparison."""
    from agent.tools import bigquery_toolset as bq

    body = await request.json()
    sql = (body or {}).get("sql")
    column = (body or {}).get("column", "value")
    if not sql or not isinstance(sql, str):
        raise HTTPException(status_code=400, detail="missing 'sql' in request body")
    result = bq.run_value(sql, column)
    logger.info("query_value: status=%s value=%r", result.get("status"), result.get("value"))
    return result


@app.post("/qe/cypher/value")
async def cypher_value(request: Request) -> dict:
    """Run a read-only Cypher query and return the scalar ``column`` (default
    ``value``). Used by cross-system checks to capture a Neo4j value."""
    from agent.tools import neo4j_toolset as neo

    body = await request.json()
    cypher = (body or {}).get("cypher")
    column = (body or {}).get("column", "value")
    if not cypher or not isinstance(cypher, str):
        raise HTTPException(status_code=400, detail="missing 'cypher' in request body")
    result = neo.run_value(cypher, column)
    logger.info("cypher_value: status=%s value=%r", result.get("status"), result.get("value"))
    return result


# --- BDD authoring / Jira / Harness -----------------------------------------

@app.get("/qe/jira/{ticket}/acceptance-criteria")
def get_jira_ac(ticket: str) -> dict:
    """Read acceptance-criteria bullets from a Jira ticket."""
    return jira_read_acceptance_criteria(ticket)


@app.post("/qe/jira/{ticket}/author")
def author_from_jira(ticket: str, dry_run: bool = False) -> dict:
    """Generate a .feature file, create Jira Test subtasks, and open a PR."""
    return author_bdd_scenarios(ticket, dry_run=dry_run)


@app.post("/qe/jira/{ticket}/sync-results")
async def sync_jira(ticket: str, request: Request, cucumber_json_path: str | None = None,
                    execution_url: str | None = None) -> dict:
    """Push Cucumber results back to Jira parent ticket + Test subtasks.

    The Harness CI step POSTs the cucumber.json content as the request body
    (the agent pod cannot read the CI workspace filesystem). `cucumber_json_path`
    remains supported as a local-file fallback for the CLI.
    """
    body = await request.body()
    report = None
    if body and body.strip():
        try:
            report = json.loads(body)
        except json.JSONDecodeError as exc:
            logger.error("sync_jira: ticket=%s invalid cucumber JSON body: %s", ticket, exc)
            raise HTTPException(status_code=400, detail=f"invalid cucumber JSON body: {exc}") from exc
    logger.info("sync_jira: ticket=%s body_bytes=%d path=%s", ticket, len(body or b""), cucumber_json_path)
    try:
        return jira_sync_results(ticket, cucumber_json_path, execution_url, report=report)
    except FileNotFoundError as exc:
        logger.error("sync_jira: ticket=%s cucumber file not found: %s", ticket, exc)
        raise HTTPException(
            status_code=400,
            detail=("cucumber report not found on the agent; POST the cucumber.json "
                    f"content in the request body instead of a path: {exc}"),
        ) from exc
    except ValueError as exc:
        logger.error("sync_jira: ticket=%s bad request: %s", ticket, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/qe/harness/bdd/latest")
def harness_bdd_latest() -> dict:
    """Latest BDD pipeline execution status from Harness."""
    return harness_latest_bdd()


@app.post("/qe/jira/{ticket}/reconcile")
def reconcile(ticket: str, plan_execution_id: str | None = None) -> dict:
    """Inspect a failing BDD run and raise a PR with refreshed Gherkin."""
    return update_bdd_from_failure(ticket, plan_execution_id)


# --- Jira webhook listener --------------------------------------------------
#
# Configure a Jira "Issue created" + "Issue updated" webhook pointing at:
#   https://qe-agent.astom.tools/qe/jira/webhook?token=<JIRA_WEBHOOK_TOKEN>
# The agent will:
#   * author BDD scenarios (and a PR) when a Story/Task/Bug is created with AC
#   * trigger the Harness `bdd_tests` pipeline when the ticket transitions
#     into the configured "Testing" status (default: "Testing").
@app.post("/qe/jira/webhook")
async def handle_jira_webhook(request: Request, token: str | None = None) -> dict:
    logger.debug("webhook received from %s", request.client)
    if not jira_webhook.verify_token(token):
        logger.warning("webhook rejected: invalid token (client=%s)", request.client)
        raise HTTPException(status_code=401, detail="invalid webhook token")
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("webhook payload parse error: %s", exc)
        raise HTTPException(status_code=400, detail=f"invalid JSON payload: {exc}") from exc
    if not isinstance(payload, dict):
        logger.error("webhook payload is not a JSON object: type=%s", type(payload).__name__)
        raise HTTPException(status_code=400, detail="payload must be a JSON object")
    logger.debug("webhook payload: %s", payload)
    result = jira_webhook.handle_event(payload)
    logger.info("webhook handled: action=%s ticket=%s", result.get("action"), result.get("ticket"))
    return result


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")  # noqa: S104  # nosec B104
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
