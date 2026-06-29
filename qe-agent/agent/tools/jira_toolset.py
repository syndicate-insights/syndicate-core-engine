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
JIRA_PROJECT = _env("JIRA_PROJECT")    # e.g. PROJ
JIRA_TEST_ISSUETYPE = _env("JIRA_TEST_ISSUETYPE", "Test")
JIRA_AC_FIELD = _env("JIRA_AC_FIELD", "")  # optional custom field id (e.g. customfield_10100)
# Per Test-subtask transitions applied after a BDD run syncs results:
#   * scenario passed -> JIRA_TEST_PASS_STATUS (default "Done")
#   * scenario failed  -> JIRA_TEST_FAIL_STATUS (default "In Progress")
# The parent ticket is intentionally left untouched (it stays in "Testing");
# only the individual subtasks are transitioned and commented on.
JIRA_TEST_PASS_STATUS = _env("JIRA_TEST_PASS_STATUS", "Done")
JIRA_TEST_FAIL_STATUS = _env("JIRA_TEST_FAIL_STATUS", "In Progress")


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
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310
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
    saw_marker = False  # whether this section uses real bullet markers
    for line in section.splitlines():
        line = line.strip()
        if not line:
            continue
        # Stop at the next major header in the description.
        if m and re.match(r"^[A-Z][A-Za-z ]{2,}:\s*$", line):
            break
        match = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*)", line)
        if match:
            bullets.append(match.group(1).strip())
            saw_marker = True
        elif saw_marker and bullets:
            # A non-marker line inside a bulleted list is a wrapped continuation
            # of the previous bullet (Jira soft-wraps long AC lines). Join it so
            # the full criterion reaches the generator — otherwise the AC is
            # truncated and the generated check is wrong.
            bullets[-1] = f"{bullets[-1]} {line}".strip()
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


def create_check_subtask(ticket: str, scenario_id: str, title: str,
                         acceptance_criteria: str, suite: str) -> dict:
    """Create a fixed non-BDD check subtask (CodingStandards / StaticAnalysis /
    NonFunctional) under the parent ticket.

    Unlike ``create_test_issue`` these have no Gherkin — Harness runs them via
    the agent's ``/qe/scenario`` API, not Cucumber. The scenario id is the first
    token of the summary (e.g. ``"CS1 - ..."``) so results can be synced back by
    matching on it.
    """
    logger.info("create_check_subtask: parent=%s scenario=%s", ticket, scenario_id)
    if not JIRA_PROJECT:
        raise RuntimeError("JIRA_PROJECT must be set.")
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT},
            "parent": {"key": ticket},
            "summary": f"{scenario_id} - {title}",
            "issuetype": {"name": JIRA_TEST_ISSUETYPE},
            "labels": list({"qe-agent", "non-bdd", suite}),
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "heading", "attrs": {"level": 3},
                     "content": [{"type": "text", "text": "Acceptance criteria"}]},
                    {"type": "paragraph",
                     "content": [{"type": "text", "text": acceptance_criteria}]},
                ],
            },
        }
    }
    created = _request("POST", "/rest/api/3/issue", payload)
    if "error" in created:
        logger.error("create_check_subtask: parent=%s scenario=%s failed: %s", ticket, scenario_id, created)
        return {"ticket": ticket, "scenario_id": scenario_id, "error": created}
    logger.info("create_check_subtask: created key=%s parent=%s scenario=%s",
                created.get("key"), ticket, scenario_id)
    return {"key": created.get("key"), "self": created.get("self"),
            "parent": ticket, "scenario_id": scenario_id}


def _find_test_subtasks(ticket: str) -> list[dict]:
    # Jira Cloud removed the legacy GET /rest/api/3/search on 2025-05-01; use the
    # replacement enhanced-search endpoint /rest/api/3/search/jql. Both return an
    # ``issues`` array, so downstream parsing is unchanged.
    jql = quote(f'parent = "{ticket}" ORDER BY created ASC')
    fields = quote("summary,status,issuetype,parent")
    resp = _request("GET", f"/rest/api/3/search/jql?jql={jql}&maxResults=100&fields={fields}")
    if isinstance(resp, dict) and "error" in resp:
        logger.error("_find_test_subtasks: ticket=%s search failed: %s", ticket, resp)
        return []
    return resp.get("issues", []) if isinstance(resp, dict) else []


def already_authored(ticket: str) -> bool:
    """True when this ticket already has agent-authored BDD Test subtasks.

    Used to make authoring idempotent so a re-delivered / retried Jira webhook
    doesn't create duplicate subtasks and PRs. Detects the agent's own
    ``"BDD AC..."`` subtasks specifically (ignores any human-created ones).
    """
    for issue in _find_test_subtasks(ticket):
        summary = (issue.get("fields") or {}).get("summary") or ""
        if summary.startswith("BDD AC"):
            return True
    return False


def _extract_ac_index(text: str) -> int | None:
    m = re.search(r"\bAC(\d+)\b", text or "", flags=re.I)
    return int(m.group(1)) if m else None


def _scenario_tags(elem: dict) -> set[str]:
    """Return a Cucumber scenario's tag names without the leading ``@``."""
    return {(tag.get("name") or "").lstrip("@") for tag in (elem.get("tags") or [])}


def _scenario_test_key(elem: dict, valid_keys: set[str]) -> str | None:
    """Return the Jira Test subtask key tagged on a Cucumber scenario.

    The authoring step tags each scenario with its backing subtask key
    (e.g. ``@PROJ-123``). The Cucumber JSON exposes those as ``tags`` on the
    scenario element. We only accept a tag that matches a known subtask key so
    the parent ticket / ``@JiraGenerated`` tags are never mistaken for one.
    """
    for name in _scenario_tags(elem):
        if name in valid_keys:
            return name
    return None


def _scenario_belongs(elem: dict, ticket: str, valid_keys: set[str]) -> bool:
    """True when a Cucumber scenario belongs to ``ticket``.

    A scenario belongs when it is tagged with the parent ticket key
    (``@PROJ-123``) or with one of that ticket's Test subtask keys. Scenarios
    from other tickets and the shared curated suites are ignored, so a foreign
    ``ACn`` index can never hijack this ticket's subtasks.
    """
    tags = _scenario_tags(elem)
    return ticket in tags or bool(tags & valid_keys)


def _comment_issue(issue_key: str, text: str, code_block: str | None = None) -> dict:
    content: list[dict] = [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]
    if code_block:
        content.append({
            "type": "codeBlock",
            "attrs": {"language": "json"},
            "content": [{"type": "text", "text": code_block}],
        })
    return _request("POST", f"/rest/api/3/issue/{quote(issue_key)}/comment", {
        "body": {"type": "doc", "version": 1, "content": content},
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


def _find_subtask_by_scenario_id(subtasks: list[dict], scenario_id: str) -> dict | None:
    """Return the subtask whose summary starts with ``scenario_id`` (e.g. "CS1").

    Fixed non-BDD check subtasks are created as ``"CS1 - <title>"``, so the
    scenario id is the leading token.
    """
    pat = re.compile(rf"^\s*{re.escape(scenario_id)}\b", re.I)
    for issue in subtasks:
        if pat.search((issue.get("fields") or {}).get("summary") or ""):
            return issue
    return None


def sync_scenario_result(ticket: str, scenario_id: str, status: str,
                         result: dict | None = None,
                         execution_url: str | None = None) -> dict:
    """Sync a single non-BDD check result (CS/SA/N) to its fixed subtask.

    Mirrors the per-subtask behaviour of ``sync_cucumber_results``:
      * PASS -> comment + JIRA_TEST_PASS_STATUS (default "Done")
      * not PASS -> comment + JIRA_TEST_FAIL_STATUS (default "In Progress")
    On failure the comment also carries the full check detail (findings,
    per-file violations and metrics) as a JSON code block so the fix is
    actionable from the ticket. The parent ticket is left untouched. No-ops
    cleanly when no ticket / no matching subtask is found.
    """
    status = "PASS" if str(status).upper() == "PASS" else "FAIL"
    result = result or {}
    if not ticket:
        return {"scenario_id": scenario_id, "status": status, "updated": False,
                "reason": "no ticket supplied"}
    subtasks = _find_test_subtasks(ticket)
    issue = _find_subtask_by_scenario_id(subtasks, scenario_id)
    if issue is None:
        logger.warning("sync_scenario_result: ticket=%s scenario=%s has no matching subtask",
                       ticket, scenario_id)
        return {"scenario_id": scenario_id, "status": status, "updated": False,
                "reason": "no matching subtask"}
    issue_key = issue.get("key")
    comment = f"Check {scenario_id}: {status}"
    if execution_url:
        comment += f" — {execution_url}"
    for f in (result.get("findings") or [])[:5]:
        comment += f"\n- {str(f)[:300]}"
    # Attach the full check detail (pass or fail) so every subtask comment
    # carries the evidence behind the result.
    detail = {k: result[k] for k in ("status", "findings", "expected", "actual", "metrics")
              if result.get(k) is not None}
    code_block = json.dumps(detail, indent=2)[:4500] if detail else None
    _comment_issue(issue_key, comment, code_block=code_block)
    status_target = JIRA_TEST_PASS_STATUS if status == "PASS" else JIRA_TEST_FAIL_STATUS
    transition = _transition_issue(issue_key, status_target) if status_target else None
    logger.info("sync_scenario_result: ticket=%s scenario=%s subtask=%s status=%s",
                ticket, scenario_id, issue_key, status)
    return {"scenario_id": scenario_id, "issue": issue_key, "status": status,
            "transition": transition, "updated": True}


# --- Pushing Cucumber results back to Jira ------------------------------------

def sync_cucumber_results(ticket: str, cucumber_json_path: str | None = None,
                          execution_url: str | None = None,
                          report: list | str | None = None) -> dict:
    """Parse cucumber results and sync PASS/FAIL to each Jira Test subtask.

    The Cucumber report can be supplied either as already-parsed/raw JSON via
    ``report`` (used by the Harness CI step, which POSTs the file content to the
    agent because the agent pod cannot see the CI workspace filesystem) or as a
    local file path via ``cucumber_json_path`` (used by the CLI / local runs).

    Only scenarios that belong to ``ticket`` (tagged with the parent key or one
    of its Test subtask keys) are synced; scenarios from other tickets and the
    shared curated suites in the same report are ignored. Each matched scenario's
    result is written to its Test subtask as a comment and a status transition:
      * scenario passed -> JIRA_TEST_PASS_STATUS (default "Done")
      * scenario failed  -> JIRA_TEST_FAIL_STATUS (default "In Progress")
    The parent ticket is left untouched (no comment, no transition).
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

    # Only sync scenarios that belong to this ticket (tagged with the parent key
    # or one of its subtask keys). Scenarios from other tickets and the shared
    # curated suites in the same Cucumber report are ignored.
    scenarios: list[dict] = []
    ignored = 0
    for feature in report:
        for elem in feature.get("elements", []):
            if elem.get("type") != "scenario":
                continue
            if not _scenario_belongs(elem, ticket, valid_keys):
                ignored += 1
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
        "ignored": ignored,
        "execution_url": execution_url,
        "scenarios": scenarios,
    }
    logger.info("sync_cucumber_results: ticket=%s total=%d passed=%d failed=%d ignored=%d",
                ticket, summary["total"], summary["passed"], summary["failed"], ignored)

    # Results are reported per Test subtask only — the parent ticket is left
    # untouched (no summary comment, no status transition).

    # Match each scenario to its Test subtask: prefer the Jira key tagged on the
    # scenario (@PROJ-123); fall back to the ACn index for older feature files.
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
        # Attach the scenario detail (pass or fail) so every subtask comment
        # carries the evidence behind the result.
        detail = {k: scenario.get(k) for k in ("name", "status", "feature", "error")
                  if scenario.get(k) is not None}
        code_block = json.dumps(detail, indent=2)[:4500] if detail else None
        _comment_issue(issue_key, comment, code_block=code_block)
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
    # The parent ticket is intentionally not transitioned — it stays in
    # "Testing" while only the individual subtasks move to Done / In Progress.
    return summary

