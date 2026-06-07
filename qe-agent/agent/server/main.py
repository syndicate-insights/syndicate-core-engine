"""FastAPI server for the QE Quality Agent.

Two surfaces share one process:
  - ADK agent surface (`/run`, `/run_sse`, session APIs) via `get_fast_api_app`,
    used for LLM-driven triage / interactive quality sweeps.
  - Deterministic QE surface (`/qe/...`) that Harness CI calls to run a suite or
    a single scenario and gate purely on the JSON `status` field.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request

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

        return get_fast_api_app(agents_dir=AGENTS_DIR, web=True)
    except Exception:  # noqa: BLE001
        return None


app: FastAPI = _build_adk_app() or FastAPI(title="QE Quality Agent")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/qe/scenarios")
def list_scenarios() -> dict:
    """List all available suites and their scenario ids."""
    return runner.list_scenarios()


@app.get("/qe/suite/{suite}")
def run_suite(suite: str) -> dict:
    """Run every scenario in a suite. `passed` is the deterministic gate."""
    return runner.run_suite(suite)


@app.get("/qe/scenario/{suite}/{scenario_id}")
def run_scenario(suite: str, scenario_id: str) -> dict:
    """Run a single scenario by id."""
    return runner.run_scenario(suite, scenario_id)


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
def sync_jira(ticket: str, cucumber_json_path: str, execution_url: str | None = None) -> dict:
    """Push Cucumber results back to Jira parent ticket + Test subtasks."""
    return jira_sync_results(ticket, cucumber_json_path, execution_url)


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
    if not jira_webhook.verify_token(token):
        raise HTTPException(status_code=401, detail="invalid webhook token")
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid JSON payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")
    return jira_webhook.handle_event(payload)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))  # noqa: S104


if __name__ == "__main__":
    main()
