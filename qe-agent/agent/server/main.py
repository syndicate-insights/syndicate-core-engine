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

configure_logging()
logger = logging.getLogger(__name__)

from agent.scenarios import runner
from agent.server import jira_webhook
from agent.sub_agents.bdd_authoring.agent import (
    author_bdd_scenarios,
    harness_latest_bdd,
    jira_read_acceptance_criteria,
    jira_sync_results,
    update_bdd_from_failure,
)

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

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))  # noqa: S104


if __name__ == "__main__":
    main()
