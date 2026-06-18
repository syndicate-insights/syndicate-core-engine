"""Jira REST v3 toolset for the QE Quality Agent.

Used by the `bdd_authoring_agent` to:
    * read a ticket's acceptance criteria,
    * create Jira `Test` subtasks under the parent Story / Task,
    * push per-scenario PASS/FAIL results from Cucumber JSON back to Jira.

All credentials come from environment variables so the same code runs on a
developer laptop, in GKE (mounted Secret), and inside Harness CI.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


JIRA_BASE_URL = _env("JIRA_BASE_URL")  # e.g. https://yourorg.atlassian.net
JIRA_USER = _env("JIRA_USER")          # service account email
JIRA_TOKEN = _env("JIRA_API_TOKEN")    # API token
JIRA_PROJECT = _env("JIRA_PROJECT")    # e.g. SYN
JIRA_TEST_ISSUETYPE = _env("JIRA_TEST_ISSUETYPE", "Test")
JIRA_AC_FIELD = _env("JIRA_AC_FIELD", "")  # optional custom field id (e.g. customfield_10100)
JIRA_TEST_PASS_STATUS = _env("JIRA_TEST_PASS_STATUS", "")
JIRA_TEST_FAIL_STATUS = _env("JIRA_TEST_FAIL_STATUS", "")
# Parent-ticket transitions applied after a BDD run syncs results:
#   * all scenarios passed -> JIRA_TICKET_PASS_STATUS (default "Done")
#   * any scenario failed   -> JIRA_TICKET_FAIL_STATUS (default "In Progress")
JIRA_TICKET_PASS_STATUS = _env("JIRA_TICKET_PASS_STATUS", "Done")
JIRA_TICKET_FAIL_STATUS = _env("JIRA_TICKET_FAIL_STATUS", "In Progress")


def _auth_header() -> dict[str, str]:
    if not (JIRA_USER and JIRA_TOKEN):
        raise RuntimeError("JIRA_USER and JIRA_API_TOKEN must be set.")
    raw = f"{JIRA_USER}:{JIRA_TOKEN}".encode()
    return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}


def _request(method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    if not JIRA_BASE_URL:
        raise RuntimeError("JIRA_BASE_URL must be set.")
    url = f"{JIRA_BASE_URL.rstrip('/')}{path}"
    logger.debug("jira %s %s body_keys=%s", method, url, list(body.keys()) if body else None)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    for k, v in _auth_header().items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = resp.read().decode()
            result = json.loads(payload) if payload else {}
            logger.debug("jira %s %s -> 2xx response_keys=%s", method, url, list(result.keys()) if isinstance(result, dict) else type(result).__name__)
            return result
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="ignore")
        logger.error("jira %s %s -> HTTP %s: %s", method, url, exc.code, body_text)
        return {"error": exc.code, "detail": body_text}


# --- Reading acceptance criteria ----------------------------------------------

def get_issue(ticket: str) -> dict:
    """Fetch a Jira issue with its description and acceptance-criteria field."""
    fields = "summary,description,status,issuetype,labels"
    if JIRA_AC_FIELD:
        fields += f",{JIRA_AC_FIELD}"
    return _request("GET", f"/rest/api/3/issue/{quote(ticket)}?fields={fields}")


def _adf_to_text(node: Any) -> str:
    """Best-effort flattening of Jira's Atlassian Document Format to plain text."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(_adf_to_text(n) for n in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return _adf_to_text(node.get("content", []))
    return ""


def acceptance_criteria(ticket: str) -> dict:
    """Extract acceptance-criteria bullets from a Jira ticket.

    Looks at the dedicated AC custom field if configured, otherwise scans the
    description for an "Acceptance Criteria" section (Markdown or ADF).
    """
    logger.info("acceptance_criteria: fetching ticket=%s", ticket)
    issue = get_issue(ticket)
    if "error" in issue:
        logger.error("acceptance_criteria: ticket=%s fetch error=%s", ticket, issue)
        return issue
    fields = issue.get("fields", {})
    raw = ""
    if JIRA_AC_FIELD and fields.get(JIRA_AC_FIELD):
        logger.debug("acceptance_criteria: reading from custom field %s", JIRA_AC_FIELD)
        raw = _adf_to_text(fields[JIRA_AC_FIELD])
    if not raw:
        logger.debug("acceptance_criteria: custom field empty or unset, falling back to description")
        raw = _adf_to_text(fields.get("description", "")) or ""
    bullets = _extract_bullets(raw)
    logger.info(
        "acceptance_criteria: ticket=%s summary=%r issuetype=%s bullets_found=%d",
        ticket,
        fields.get("summary"),
        (fields.get("issuetype") or {}).get("name"),
        len(bullets),
    )
    if not bullets:
        logger.warning("acceptance_criteria: ticket=%s has no AC bullets — BDD authoring will no-op", ticket)
    else:
        for i, b in enumerate(bullets, 1):
            logger.debug("acceptance_criteria: ticket=%s bullet[%d]=%r", ticket, i, b)
    return {
        "ticket": ticket,
        "summary": fields.get("summary"),
        "issuetype": (fields.get("issuetype") or {}).get("name"),
        "raw": raw,
        "bullets": bullets,
    }


def _extract_bullets(text: str) -> list[str]:
    if not text:
        return []
    # Pull only the section under an "Acceptance Criteria" header if present.
    m = re.search(r"(?im)^\s*acceptance\s*criteria\s*[:\-]?\s*$", text)
    section = text[m.end():] if m else text
    bullets: list[str] = []
    for line in section.splitlines():
        line = line.strip()
        if not line:
            if bullets:
                # blank line after bullets ends the section in description-only mode
                if m is None and len(bullets) >= 1 and not section.startswith(line):
                    pass
            continue
        # Stop at the next major header in the description.
        if m and re.match(r"^[A-Z][A-Za-z ]{2,}:\s*$", line):
            break
        match = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*)", line)
        if match:
            bullets.append(match.group(1).strip())
        elif "given" in line.lower() or "when" in line.lower() or "then" in line.lower():
            bullets.append(line)
    return bullets


# --- Creating Test subtasks ---------------------------------------------------

def create_test_issue(ticket: str, summary: str, gherkin: str, labels: list[str] | None = None) -> dict:
    """Create a Jira `Test` subtask under the originating ticket."""
    logger.info("create_test_issue: parent=%s summary=%r", ticket, summary)
    if not JIRA_PROJECT:
        raise RuntimeError("JIRA_PROJECT must be set.")
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT},
            "parent": {"key": ticket},
            "summary": summary,
            "issuetype": {"name": JIRA_TEST_ISSUETYPE},
            "labels": list({"qe-agent", "cucumber", "bdd", *(labels or [])}),
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "heading", "attrs": {"level": 3},
                     "content": [{"type": "text", "text": "Gherkin"}]},
                    {"type": "codeBlock", "attrs": {"language": "gherkin"},
                     "content": [{"type": "text", "text": gherkin}]},
                ],
            },
        }
    }
    created = _request("POST", "/rest/api/3/issue", payload)
    if "error" in created:
        logger.error("create_test_issue: parent=%s failed: %s", ticket, created)
        return {"ticket": ticket, "summary": summary, "error": created}
    logger.info("create_test_issue: created key=%s parent=%s", created.get("key"), ticket)
    return {"key": created.get("key"), "self": created.get("self"), "parent": ticket}


def _find_test_subtasks(ticket: str) -> list[dict]:
    jql = quote(f'parent = "{ticket}" ORDER BY created ASC')
    fields = quote("summary,status,issuetype,parent")
    resp = _request("GET", f"/rest/api/3/search?jql={jql}&maxResults=100&fields={fields}")
    return resp.get("issues", []) if isinstance(resp, dict) else []


def _extract_ac_index(text: str) -> int | None:
    m = re.search(r"\bAC(\d+)\b", text or "", flags=re.I)
    return int(m.group(1)) if m else None


def _scenario_test_key(elem: dict, valid_keys: set[str]) -> str | None:
    """Return the Jira Test subtask key tagged on a Cucumber scenario.

    The authoring step tags each scenario with its backing subtask key
    (e.g. ``@SYN-36``). The Cucumber JSON exposes those as ``tags`` on the
    scenario element. We only accept a tag that matches a known subtask key so
    the parent ticket / ``@JiraGenerated`` tags are never mistaken for one.
    """
    for tag in elem.get("tags") or []:
        name = (tag.get("name") or "").lstrip("@")
        if name in valid_keys:
            return name
    return None


def _comment_issue(issue_key: str, text: str) -> dict:
    return _request("POST", f"/rest/api/3/issue/{quote(issue_key)}/comment", {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
        },
    })


def _transition_issue(issue_key: str, target_status: str) -> dict:
    transitions = _request("GET", f"/rest/api/3/issue/{quote(issue_key)}/transitions")
    for tr in (transitions.get("transitions") or []):
        to_name = ((tr.get("to") or {}).get("name") or "").strip().lower()
        if to_name == target_status.strip().lower():
            return _request("POST", f"/rest/api/3/issue/{quote(issue_key)}/transitions", {
                "transition": {"id": tr.get("id")}
            })
    return {"skipped": f"no transition to '{target_status}' from current status"}


# --- Pushing Cucumber results back to Jira ------------------------------------

def sync_cucumber_results(ticket: str, cucumber_json_path: str | None = None,
                          execution_url: str | None = None,
                          report: list | str | None = None) -> dict:
    """Parse cucumber results and sync PASS/FAIL to Jira parent + Test subtasks.

    The Cucumber report can be supplied either as already-parsed/raw JSON via
    ``report`` (used by the Harness CI step, which POSTs the file content to the
    agent because the agent pod cannot see the CI workspace filesystem) or as a
    local file path via ``cucumber_json_path`` (used by the CLI / local runs).

    After per-subtask comments are written, the parent ticket is transitioned:
      * all scenarios passed -> JIRA_TICKET_PASS_STATUS (default "Done")
      * any scenario failed   -> JIRA_TICKET_FAIL_STATUS (default "In Progress")
    """
    logger.info("sync_cucumber_results: ticket=%s path=%s report_inline=%s",
                ticket, cucumber_json_path, report is not None)
    if report is None:
        if not cucumber_json_path:
            raise ValueError("either `report` content or `cucumber_json_path` must be provided")
        with open(cucumber_json_path, encoding="utf-8") as fh:
            report = json.load(fh)
    if isinstance(report, (str, bytes)):
        report = json.loads(report)
    if not isinstance(report, list):
        raise ValueError("cucumber report must be a JSON array of features")

    # Index the parent's Test subtasks up front so each scenario can be matched
    # to its subtask by the Jira key tagged on it (preferred), falling back to
    # the ACn index parsed from summaries for features authored before tagging.
    subtasks = _find_test_subtasks(ticket)
    by_key: dict[str, dict] = {}
    by_index: dict[int, dict] = {}
    for issue in subtasks:
        key = issue.get("key")
        if key:
            by_key[key] = issue
        idx = _extract_ac_index((issue.get("fields") or {}).get("summary") or "")
        if idx is not None:
            by_index[idx] = issue
    valid_keys = set(by_key)

    scenarios: list[dict] = []
    for feature in report:
        for elem in feature.get("elements", []):
            if elem.get("type") != "scenario":
                continue
            steps = elem.get("steps", [])
            failed = [s for s in steps if (s.get("result") or {}).get("status") == "failed"]
            scenarios.append({
                "name": elem.get("name"),
                "status": "FAIL" if failed else "PASS",
                "ac_index": _extract_ac_index(elem.get("name") or ""),
                "test_key": _scenario_test_key(elem, valid_keys),
                "feature": feature.get("uri"),
                "error": (failed[0].get("result", {}).get("error_message") if failed else None),
            })
    summary = {
        "ticket": ticket,
        "total": len(scenarios),
        "passed": sum(1 for s in scenarios if s["status"] == "PASS"),
        "failed": sum(1 for s in scenarios if s["status"] == "FAIL"),
        "execution_url": execution_url,
        "scenarios": scenarios,
    }
    logger.info("sync_cucumber_results: ticket=%s total=%d passed=%d failed=%d",
                ticket, summary["total"], summary["passed"], summary["failed"])

    # Add a summary comment on the parent ticket.
    body_text = (
        f"BDD execution: {summary['passed']}/{summary['total']} passed"
        + (f" ({summary['failed']} failed)" if summary["failed"] else "")
        + (f" — {execution_url}" if execution_url else "")
    )
    _comment_issue(ticket, body_text)

    # Match each scenario to its Test subtask: prefer the Jira key tagged on the
    # scenario (@SYN-NN); fall back to the ACn index for older feature files.
    updates: list[dict] = []
    for scenario in scenarios:
        issue = None
        if scenario.get("test_key") and scenario["test_key"] in by_key:
            issue = by_key[scenario["test_key"]]
        else:
            idx = scenario.get("ac_index")
            if idx is not None and idx in by_index:
                issue = by_index[idx]
        if issue is None:
            updates.append({"scenario": scenario.get("name"), "updated": False, "reason": "no matching subtask"})
            continue
        issue_key = issue.get("key")
        comment = f"Scenario {scenario['name']}: {scenario['status']}"
        if execution_url:
            comment += f" — {execution_url}"
        if scenario.get("error"):
            comment += f"\nFailure: {scenario['error'][:400]}"
        _comment_issue(issue_key, comment)
        logger.info("sync_cucumber_results: subtask=%s status=%s commented", issue_key, scenario["status"])

        status_target = JIRA_TEST_PASS_STATUS if scenario["status"] == "PASS" else JIRA_TEST_FAIL_STATUS
        transition_resp = None
        if status_target:
            transition_resp = _transition_issue(issue_key, status_target)
        updates.append({
            "scenario": scenario.get("name"),
            "issue": issue_key,
            "status": scenario.get("status"),
            "matched_by": "test_key" if scenario.get("test_key") else "ac_index",
            "transition": transition_resp,
            "updated": True,
        })

    summary["subtask_updates"] = updates

    # Transition the parent ticket based on the overall outcome.
    overall_pass = summary["total"] > 0 and summary["failed"] == 0
    parent_target = JIRA_TICKET_PASS_STATUS if overall_pass else JIRA_TICKET_FAIL_STATUS
    parent_transition = None
    if parent_target:
        logger.info("sync_cucumber_results: ticket=%s overall_pass=%s -> transition to %r",
                    ticket, overall_pass, parent_target)
        parent_transition = _transition_issue(ticket, parent_target)
        if isinstance(parent_transition, dict) and parent_transition.get("error"):
            logger.error("sync_cucumber_results: ticket=%s parent transition failed: %s",
                         ticket, parent_transition)
    summary["parent_transition"] = {"target": parent_target, "result": parent_transition}
    return summary

