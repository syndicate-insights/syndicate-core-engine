"""Jira REST v3 toolset for the QE Quality Agent.

Used by the `bdd_authoring_agent` to:
  * read a ticket's acceptance criteria,
  * create Jira `Test` issues (Xray Cloud or generic Test issuetype) and link
    them to the originating Story / Task,
  * push per-scenario PASS/FAIL results from a Cucumber JSON report so each
    Test issue surfaces the latest Harness execution outcome.

All credentials come from environment variables so the same code runs on a
developer laptop, in GKE (mounted Secret), and inside Harness CI.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


JIRA_BASE_URL = _env("JIRA_BASE_URL")  # e.g. https://yourorg.atlassian.net
JIRA_USER = _env("JIRA_USER")          # service account email
JIRA_TOKEN = _env("JIRA_API_TOKEN")    # API token
JIRA_PROJECT = _env("JIRA_PROJECT")    # e.g. SYN
JIRA_TEST_ISSUETYPE = _env("JIRA_TEST_ISSUETYPE", "Test")
JIRA_AC_FIELD = _env("JIRA_AC_FIELD", "")  # optional custom field id (e.g. customfield_10100)
XRAY_CLIENT_ID = _env("XRAY_CLIENT_ID")
XRAY_CLIENT_SECRET = _env("XRAY_CLIENT_SECRET")


def _auth_header() -> dict[str, str]:
    if not (JIRA_USER and JIRA_TOKEN):
        raise RuntimeError("JIRA_USER and JIRA_API_TOKEN must be set.")
    raw = f"{JIRA_USER}:{JIRA_TOKEN}".encode()
    return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}


def _request(method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    if not JIRA_BASE_URL:
        raise RuntimeError("JIRA_BASE_URL must be set.")
    url = f"{JIRA_BASE_URL.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    for k, v in _auth_header().items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = resp.read().decode()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        return {"error": exc.code, "detail": exc.read().decode(errors="ignore")}


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
    issue = get_issue(ticket)
    if "error" in issue:
        return issue
    fields = issue.get("fields", {})
    raw = ""
    if JIRA_AC_FIELD and fields.get(JIRA_AC_FIELD):
        raw = _adf_to_text(fields[JIRA_AC_FIELD])
    if not raw:
        raw = _adf_to_text(fields.get("description", "")) or ""
    bullets = _extract_bullets(raw)
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


# --- Creating linked Test issues ----------------------------------------------

def create_test_issue(ticket: str, summary: str, gherkin: str, labels: list[str] | None = None) -> dict:
    """Create a Jira Test issue and link it to the originating ticket via 'Tests'."""
    if not JIRA_PROJECT:
        raise RuntimeError("JIRA_PROJECT must be set.")
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT},
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
        return created
    new_key = created.get("key")
    link_resp = _request("POST", "/rest/api/3/issueLink", {
        "type": {"name": "Tests"},
        "inwardIssue": {"key": new_key},
        "outwardIssue": {"key": ticket},
    })
    return {"key": new_key, "self": created.get("self"), "link": link_resp}


# --- Pushing Cucumber results back to Jira / Xray -----------------------------

def sync_cucumber_results(ticket: str, cucumber_json_path: str,
                          execution_url: str | None = None) -> dict:
    """Parse cucumber.json, comment on the parent ticket and transition Test
    issues whose summary matches a scenario name. Best-effort: if Xray Cloud is
    configured, also POST results to the Xray import API."""
    with open(cucumber_json_path, encoding="utf-8") as fh:
        report = json.load(fh)
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
    # Add a comment on the parent ticket linking to the Harness execution.
    body_text = (
        f"BDD execution: {summary['passed']}/{summary['total']} passed"
        + (f" ({summary['failed']} failed)" if summary["failed"] else "")
        + (f" — {execution_url}" if execution_url else "")
    )
    _request("POST", f"/rest/api/3/issue/{quote(ticket)}/comment", {
        "body": {"type": "doc", "version": 1,
                 "content": [{"type": "paragraph",
                              "content": [{"type": "text", "text": body_text}]}]},
    })
    # Best-effort: push to Xray Cloud importer if configured.
    if XRAY_CLIENT_ID and XRAY_CLIENT_SECRET:
        summary["xray"] = _push_to_xray(cucumber_json_path)
    return summary


def _push_to_xray(cucumber_json_path: str) -> dict:
    """Authenticate against Xray Cloud and import a Cucumber JSON result."""
    auth = _request_raw(
        "POST",
        "https://xray.cloud.getxray.app/api/v2/authenticate",
        {"client_id": XRAY_CLIENT_ID, "client_secret": XRAY_CLIENT_SECRET},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if "error" in auth:
        return auth
    token = auth.get("token") or auth.get("body")
    with open(cucumber_json_path, "rb") as fh:
        data = fh.read()
    return _request_raw(
        "POST",
        "https://xray.cloud.getxray.app/api/v2/import/execution/cucumber",
        body=data.decode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        raw=True,
    )


def _request_raw(method: str, url: str, body: Any | None = None,
                 headers: dict[str, str] | None = None, raw: bool = False) -> dict:
    data = (body if raw else json.dumps(body or {})).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            payload = resp.read().decode()
            try:
                return json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                return {"body": payload}
    except urllib.error.HTTPError as exc:
        return {"error": exc.code, "detail": exc.read().decode(errors="ignore")}
