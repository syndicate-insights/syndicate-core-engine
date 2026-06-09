"""Jira webhook handler.

Jira Cloud sends a JSON payload on every issue lifecycle event. This module
inspects the event, decides whether the QE agent should:

  * `jira:issue_created` → author BDD scenarios from the ticket's acceptance
    criteria (idempotent — does nothing when the ticket has no AC bullets).
  * `jira:issue_updated` with a status transition into the "Testing" status →
    trigger the Harness BDD pipeline, scoped to that ticket, via the pre-issued
    Custom Webhook URL.

The endpoint is protected by a shared `?token=` query parameter (rotatable via
the `JIRA_WEBHOOK_TOKEN` Secret) because Jira Cloud does not sign payloads.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agent.sub_agents.bdd_authoring.agent import author_bdd_scenarios
from agent.tools import harness_toolset as harness

logger = logging.getLogger(__name__)


JIRA_WEBHOOK_TOKEN = os.environ.get("JIRA_WEBHOOK_TOKEN", "")
JIRA_TESTING_STATUS = os.environ.get("JIRA_TESTING_STATUS", "Testing")
JIRA_TRIGGER_ISSUETYPES = {
    s.strip()
    for s in os.environ.get("JIRA_TRIGGER_ISSUETYPES", "Story,Task,Bug").split(",")
    if s.strip()
}


def verify_token(token: str | None) -> bool:
    """Return True when the request's ?token=... matches the configured secret.

    Disabled (always True) only when no token is configured — useful for local
    development; production deployments must set JIRA_WEBHOOK_TOKEN.
    """
    if not JIRA_WEBHOOK_TOKEN:
        logger.debug("token verification skipped (JIRA_WEBHOOK_TOKEN not set)")
        return True
    valid = bool(token) and token == JIRA_WEBHOOK_TOKEN
    if not valid:
        logger.warning("token verification failed (token present=%s)", bool(token))
    return valid


def handle_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a Jira webhook payload."""
    event = payload.get("webhookEvent") or payload.get("issue_event_type_name")
    issue = payload.get("issue") or {}
    key = issue.get("key")
    fields = issue.get("fields") or {}
    issuetype = (fields.get("issuetype") or {}).get("name")
    logger.info("handle_event event=%s ticket=%s issuetype=%s", event, key, issuetype)

    if not key:
        logger.warning("handle_event: no issue key in payload — ignored")
        return {"action": "ignored", "reason": "no issue key in payload"}
    if JIRA_TRIGGER_ISSUETYPES and issuetype not in JIRA_TRIGGER_ISSUETYPES:
        logger.info("handle_event: issuetype %r not in trigger list %s — ignored", issuetype, JIRA_TRIGGER_ISSUETYPES)
        return {"action": "ignored", "reason": f"issuetype {issuetype} not in trigger list",
                "ticket": key, "event": event}

    if event in ("jira:issue_created", "issue_created"):
        logger.info("handle_event: dispatching BDD authoring for ticket=%s", key)
        return _author_bdd(key)

    if event in ("jira:issue_updated", "issue_updated"):
        transitioned = _transitioned_to(payload, JIRA_TESTING_STATUS)
        logger.debug("handle_event: ticket=%s transitioned_to_testing=%s", key, transitioned)
        if transitioned:
            logger.info("handle_event: dispatching BDD pipeline trigger for ticket=%s", key)
            return _trigger_bdd(key)
        return {"action": "ignored", "reason": "no status transition to testing",
                "ticket": key, "event": event}

    logger.debug("handle_event: unhandled event=%s ticket=%s — ignored", event, key)
    return {"action": "ignored", "reason": f"unhandled event {event}", "ticket": key}


def _author_bdd(ticket: str) -> dict[str, Any]:
    logger.info("_author_bdd: starting BDD authoring for ticket=%s", ticket)
    result = author_bdd_scenarios(ticket)
    if result.get("error"):
        logger.error("_author_bdd: ticket=%s error=%s", ticket, result["error"])
    else:
        logger.info(
            "_author_bdd: ticket=%s feature_path=%s test_issues=%s pr=%s",
            ticket,
            result.get("feature_path"),
            [t.get("key") for t in result.get("test_issues", []) if isinstance(t, dict)],
            (result.get("pr") or {}).get("html_url"),
        )
    return {
        "action": "authored",
        "ticket": ticket,
        "feature_path": result.get("feature_path"),
        "test_issues": [t.get("key") for t in result.get("test_issues", []) if isinstance(t, dict)],
        "pr": (result.get("pr") or {}).get("html_url"),
        "error": result.get("error"),
    }


def _trigger_bdd(ticket: str) -> dict[str, Any]:
    logger.info("_trigger_bdd: firing BDD pipeline for ticket=%s", ticket)
    fired = harness.trigger_bdd_for_ticket(ticket)
    logger.info("_trigger_bdd: ticket=%s harness_response=%s", ticket, fired)
    return {"action": "triggered_bdd_pipeline", "ticket": ticket, "harness": fired}


def _transitioned_to(payload: dict[str, Any], target_status: str) -> bool:
    """Inspect the changelog to detect a status transition into target_status."""
    changelog = payload.get("changelog") or {}
    for item in changelog.get("items", []) or []:
        if item.get("field") == "status" and item.get("toString") == target_status:
            return True
    # Some Jira variants put the transition under transition.toStatus
    transition = payload.get("transition") or {}
    return transition.get("toStatus") == target_status
