"""Harness NextGen API toolset.

Lets the agent look up the latest pipeline execution for the BDD pipeline,
fetch the per-step status, and read JUnit / Cucumber artifacts produced by the
`syndicate-bdd-tests` pipeline. Used by the `bdd_authoring_agent` to decide
whether a failing run is a regression in the pipeline (raise an incident) or a
stale Gherkin scenario (raise a PR against `bdd-tests/`).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlencode


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


HARNESS_BASE_URL = _env("HARNESS_BASE_URL", "https://app.harness.io")
HARNESS_API_KEY = _env("HARNESS_API_KEY")
HARNESS_ACCOUNT = _env("HARNESS_ACCOUNT_ID")
HARNESS_ORG = _env("HARNESS_ORG_ID", "default")
HARNESS_PROJECT = _env("HARNESS_PROJECT_ID")
HARNESS_BDD_PIPELINE = _env("HARNESS_BDD_PIPELINE_ID", "bdd_tests")
# Pre-issued Custom Webhook URL for the BDD trigger
# (.harness/.../triggers/jira_testing_transition.yaml). The agent uses this
# URL when a Jira ticket transitions into the "Testing" status so it doesn't
# need a Harness API key for the trigger path. Treat this URL as a secret.
HARNESS_BDD_WEBHOOK_URL = _env("HARNESS_BDD_WEBHOOK_URL")


def _request(method: str, path: str, query: dict | None = None,
             body: dict | None = None, timeout: int = 30) -> dict:
    if not HARNESS_API_KEY:
        raise RuntimeError("HARNESS_API_KEY must be set.")
    qs = ("?" + urlencode({"accountIdentifier": HARNESS_ACCOUNT, **(query or {})})
          if HARNESS_ACCOUNT else ("?" + urlencode(query) if query else ""))
    url = f"{HARNESS_BASE_URL.rstrip('/')}{path}{qs}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310
    req.add_header("x-api-key", HARNESS_API_KEY)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = resp.read().decode()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        return {"error": exc.code, "detail": exc.read().decode(errors="ignore")}


def list_executions(pipeline: str | None = None, page_size: int = 5) -> dict:
    """List the most recent executions of the BDD pipeline."""
    pid = pipeline or HARNESS_BDD_PIPELINE
    return _request(
        "POST",
        "/pipeline/api/pipelines/execution/summary",
        query={
            "orgIdentifier": HARNESS_ORG,
            "projectIdentifier": HARNESS_PROJECT,
            "pipelineIdentifier": pid,
            "size": page_size,
        },
        body={"filterType": "PipelineExecution"},
    )


def get_execution(plan_execution_id: str) -> dict:
    """Get the full execution graph (per step status, logs URL, artifacts)."""
    return _request(
        "GET",
        f"/pipeline/api/pipelines/execution/v2/{plan_execution_id}",
        query={"orgIdentifier": HARNESS_ORG, "projectIdentifier": HARNESS_PROJECT},
    )


def latest_bdd_status(pipeline: str | None = None) -> dict:
    """Convenience: status of the most recent BDD execution + a UI link."""
    pid = pipeline or HARNESS_BDD_PIPELINE
    summary = list_executions(pid, page_size=1)
    rows = (((summary.get("data") or {}).get("content")) or [])
    if not rows:
        return {"pipeline": pid, "status": "UNKNOWN", "reason": "no executions found"}
    row = rows[0]
    plan_id = row.get("planExecutionId")
    return {
        "pipeline": pid,
        "execution_id": plan_id,
        "status": row.get("status"),
        "trigger": (row.get("executionTriggerInfo") or {}).get("triggerType"),
        "started_at": row.get("startTs"),
        "ended_at": row.get("endTs"),
        "ui_url": (
            f"{HARNESS_BASE_URL}/ng/account/{HARNESS_ACCOUNT}/cd/orgs/{HARNESS_ORG}"
            f"/projects/{HARNESS_PROJECT}/pipelines/{pid}/executions/{plan_id}/pipeline"
            if plan_id and HARNESS_ACCOUNT else None
        ),
    }


def trigger_bdd_for_ticket(ticket: str) -> dict:
    """Fire the BDD pipeline's Custom Webhook trigger with a Jira ticket key.

    Used by the agent's Jira webhook listener when a ticket transitions to the
    "Testing" status. The Harness Custom Webhook URL itself is the auth, so the
    agent doesn't need a Harness API key for this path.
    """
    if not HARNESS_BDD_WEBHOOK_URL:
        return {"error": "HARNESS_BDD_WEBHOOK_URL not configured"}
    body = json.dumps({"issue": {"key": ticket}}).encode()
    req = urllib.request.Request(  # noqa: S310
        HARNESS_BDD_WEBHOOK_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            payload = resp.read().decode()
            try:
                return {"status": "queued", "ticket": ticket,
                        "response": json.loads(payload) if payload else {}}
            except json.JSONDecodeError:
                return {"status": "queued", "ticket": ticket, "response": payload}
    except urllib.error.HTTPError as exc:
        return {"error": exc.code, "detail": exc.read().decode(errors="ignore")}
