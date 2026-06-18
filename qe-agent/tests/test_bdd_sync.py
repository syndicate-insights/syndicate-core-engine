"""Unit tests for BDD result syncing to Jira Test subtasks.

These cover the deterministic, network-free parts:
  * `gherkin.feature_for_ticket` tagging scenarios with their Test subtask key,
  * `jira_toolset.sync_cucumber_results` matching each Cucumber scenario to the
    right subtask by that key, falling back to the ACn index for older features.

Jira REST calls are monkeypatched so no network / credentials are needed.
"""

from __future__ import annotations

from agent.sub_agents.bdd_authoring import gherkin
from agent.tools import jira_toolset


def test_feature_tags_scenarios_with_test_keys():
    feat = gherkin.feature_for_ticket(
        "SYN-99", "Some summary", ["first AC", "second AC"],
        test_keys=["SYN-101", "SYN-102"],
    )
    assert "@JiraGenerated @SYN-99 @SYN-101" in feat
    assert "@JiraGenerated @SYN-99 @SYN-102" in feat


def test_feature_without_keys_has_no_subtask_tag():
    feat = gherkin.feature_for_ticket("SYN-99", "Some summary", ["first AC"])
    assert "@JiraGenerated @SYN-99\n" in feat


def _stub_jira(monkeypatch, subtasks):
    """Stub Jira REST helpers, recording comment/transition targets.

    Returns a dict with ``comments`` and ``transitions`` lists of (key, value)
    so tests can assert exactly which issues were touched.
    """
    calls: dict[str, list] = {"comments": [], "transitions": []}
    monkeypatch.setattr(jira_toolset, "_find_test_subtasks", lambda ticket: subtasks)
    monkeypatch.setattr(jira_toolset, "_comment_issue",
                        lambda key, text: calls["comments"].append((key, text)) or {})
    monkeypatch.setattr(jira_toolset, "_transition_issue",
                        lambda key, status: calls["transitions"].append((key, status)) or {"ok": True})
    return calls


def test_sync_matches_by_test_key(monkeypatch):
    calls = _stub_jira(monkeypatch, [
        {"key": "SYN-101", "fields": {"summary": "BDD AC1 for SYN-99: x"}},
        {"key": "SYN-102", "fields": {"summary": "BDD AC2 for SYN-99: y"}},
    ])
    report = [{
        "uri": "f.feature",
        "elements": [
            # AC1 scenario is tagged with SYN-102: the key must win over the index.
            {"type": "scenario", "name": "AC1 - first",
             "tags": [{"name": "@JiraGenerated"}, {"name": "@SYN-99"}, {"name": "@SYN-102"}],
             "steps": [{"result": {"status": "passed"}}]},
            {"type": "scenario", "name": "AC2 - second",
             "tags": [{"name": "@SYN-101"}],
             "steps": [{"result": {"status": "failed", "error_message": "boom"}}]},
        ],
    }]
    res = jira_toolset.sync_cucumber_results("SYN-99", report=report, execution_url="http://exec")
    ups = {u["scenario"]: u for u in res["subtask_updates"]}
    assert ups["AC1 - first"]["issue"] == "SYN-102"
    assert ups["AC1 - first"]["matched_by"] == "test_key"
    assert ups["AC2 - second"]["issue"] == "SYN-101"
    assert ups["AC2 - second"]["status"] == "FAIL"

    # Comments and transitions go to the subtasks only — never the parent SYN-99.
    commented = {k for k, _ in calls["comments"]}
    transitioned = dict(calls["transitions"])
    assert commented == {"SYN-101", "SYN-102"}
    assert "SYN-99" not in commented
    assert "SYN-99" not in transitioned
    # Passing scenario -> Done; failing scenario -> In Progress.
    assert transitioned["SYN-102"] == "Done"
    assert transitioned["SYN-101"] == "In Progress"


def test_sync_falls_back_to_ac_index(monkeypatch):
    _stub_jira(monkeypatch, [
        {"key": "SYN-101", "fields": {"summary": "BDD AC1 for SYN-99: x"}},
        {"key": "SYN-102", "fields": {"summary": "BDD AC2 for SYN-99: y"}},
    ])
    report = [{
        "uri": "f.feature",
        "elements": [
            # No subtask-key tag -> match on the ACn index parsed from the name.
            {"type": "scenario", "name": "AC2 - second",
             "tags": [{"name": "@JiraGenerated"}, {"name": "@SYN-99"}],
             "steps": [{"result": {"status": "passed"}}]},
        ],
    }]
    res = jira_toolset.sync_cucumber_results("SYN-99", report=report)
    update = res["subtask_updates"][0]
    assert update["issue"] == "SYN-102"
    assert update["matched_by"] == "ac_index"


def test_sync_reports_unmatched_scenario(monkeypatch):
    _stub_jira(monkeypatch, [
        {"key": "SYN-101", "fields": {"summary": "BDD AC1 for SYN-99: x"}},
    ])
    report = [{
        "uri": "f.feature",
        "elements": [
            {"type": "scenario", "name": "free-form scenario with no AC index",
             "tags": [{"name": "@JiraGenerated"}],
             "steps": [{"result": {"status": "passed"}}]},
        ],
    }]
    res = jira_toolset.sync_cucumber_results("SYN-99", report=report)
    update = res["subtask_updates"][0]
    assert update["updated"] is False
    assert update["reason"] == "no matching subtask"
