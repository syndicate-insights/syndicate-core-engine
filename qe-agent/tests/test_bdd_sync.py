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
                        lambda key, text, code_block=None:
                            calls["comments"].append((key, text, code_block)) or {})
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
    commented = {c[0] for c in calls["comments"]}
    transitioned = dict(calls["transitions"])
    assert commented == {"SYN-101", "SYN-102"}
    assert "SYN-99" not in commented
    assert "SYN-99" not in transitioned
    # Passing scenario -> Done; failing scenario -> In Progress.
    assert transitioned["SYN-102"] == "Done"
    assert transitioned["SYN-101"] == "In Progress"
    # Every subtask comment carries the scenario detail (pass and fail).
    detail_by_issue = {c[0]: c[2] for c in calls["comments"]}
    assert detail_by_issue["SYN-102"] and '"status": "PASS"' in detail_by_issue["SYN-102"]
    assert detail_by_issue["SYN-101"] and '"status": "FAIL"' in detail_by_issue["SYN-101"]


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
            # Belongs to SYN-99 (parent tag) but has no AC index / subtask key,
            # so it can't be matched to a subtask.
            {"type": "scenario", "name": "free-form scenario with no AC index",
             "tags": [{"name": "@JiraGenerated"}, {"name": "@SYN-99"}],
             "steps": [{"result": {"status": "passed"}}]},
        ],
    }]
    res = jira_toolset.sync_cucumber_results("SYN-99", report=report)
    update = res["subtask_updates"][0]
    assert update["updated"] is False
    assert update["reason"] == "no matching subtask"


def test_sync_ignores_foreign_and_suite_scenarios(monkeypatch):
    """Scenarios from other tickets / curated suites must not touch subtasks."""
    calls = _stub_jira(monkeypatch, [
        {"key": "SYN-44", "fields": {"summary": "BDD AC1 for SYN-43: x"}},
        {"key": "SYN-45", "fields": {"summary": "BDD AC2 for SYN-43: y"}},
    ])
    report = [{
        "uri": "f.feature",
        "elements": [
            {"type": "scenario", "name": "AC1 - first",
             "tags": [{"name": "@JiraGenerated"}, {"name": "@SYN-43"}],
             "steps": [{"result": {"status": "passed"}}]},
            {"type": "scenario", "name": "AC2 - second",
             "tags": [{"name": "@JiraGenerated"}, {"name": "@SYN-43"}],
             "steps": [{"result": {"status": "failed", "error_message": "boom"}}]},
            # Foreign ticket scenario sharing the AC1 index must NOT hijack SYN-44.
            {"type": "scenario", "name": "AC1 - foreign",
             "tags": [{"name": "@JiraGenerated"}, {"name": "@SYN-7"}],
             "steps": [{"result": {"status": "failed", "error_message": "nope"}}]},
            # Curated suite scenario with no SYN tag is ignored.
            {"type": "scenario", "name": "CS1 - dbt naming",
             "tags": [{"name": "@Standards"}, {"name": "@DbtNaming"}],
             "steps": [{"result": {"status": "passed"}}]},
        ],
    }]
    res = jira_toolset.sync_cucumber_results("SYN-43", report=report)
    assert res["total"] == 2
    assert res["passed"] == 1 and res["failed"] == 1
    assert res["ignored"] == 2

    commented = {c[0] for c in calls["comments"]}
    transitioned = dict(calls["transitions"])
    assert commented == {"SYN-44", "SYN-45"}
    assert transitioned["SYN-44"] == "Done"          # AC1 passed
    assert transitioned["SYN-45"] == "In Progress"   # AC2 failed -> stays In Progress
    # The foreign failing AC1 must never have transitioned SYN-44.
    assert calls["transitions"].count(("SYN-44", "In Progress")) == 0


def test_find_test_subtasks_uses_enhanced_search_endpoint(monkeypatch):
    """Subtask lookup must hit the new /rest/api/3/search/jql endpoint.

    Jira Cloud removed the legacy /rest/api/3/search on 2025-05-01; using it
    returns an error and silently yields no subtasks (so results never sync).
    """
    seen = {}

    def fake_request(method, path, *args, **kwargs):
        seen["method"], seen["path"] = method, path
        return {"issues": [{"key": "SYN-44", "fields": {"summary": "BDD AC1 for SYN-43: x"}}]}

    monkeypatch.setattr(jira_toolset, "_request", fake_request)
    issues = jira_toolset._find_test_subtasks("SYN-43")
    assert "/rest/api/3/search/jql?" in seen["path"]
    assert "/rest/api/3/search?" not in seen["path"]
    assert issues[0]["key"] == "SYN-44"


def test_find_test_subtasks_returns_empty_on_error(monkeypatch):
    monkeypatch.setattr(jira_toolset, "_request", lambda *a, **k: {"error": 410, "detail": "gone"})
    assert jira_toolset._find_test_subtasks("SYN-43") == []


# --- Non-BDD check sync (CodingStandards / StaticAnalysis / NonFunctional) ----

def test_sync_scenario_result_matches_by_scenario_id(monkeypatch):
    calls = _stub_jira(monkeypatch, [
        {"key": "SYN-50", "fields": {"summary": "CS1 - dbt model naming convention"}},
        {"key": "SYN-51", "fields": {"summary": "SA3 - Python security scan (bandit)"}},
        {"key": "SYN-52", "fields": {"summary": "N1 - Performance / SLA"}},
    ])
    # PASS -> Done, with the result detail attached too (every comment carries it)
    r = jira_toolset.sync_scenario_result(
        "SYN-43", "CS1", "PASS",
        result={"status": "PASS", "metrics": {"files_checked": 9, "violations": 0}},
        execution_url="http://e")
    assert r["issue"] == "SYN-50" and r["status"] == "PASS"
    pass_comment = next(c for c in calls["comments"] if c[0] == "SYN-50")
    assert pass_comment[2] is not None  # detail attached on PASS too
    assert '"violations": 0' in pass_comment[2]
    # FAIL -> In Progress, with the full check detail attached as a JSON block
    fail_result = {
        "status": "FAIL",
        "findings": ["sqlfluff reported 18 style violations."],
        "actual": [{"file": "a.sql", "violations": 6}],
        "metrics": {"files_checked": 9, "violations": 18},
    }
    r = jira_toolset.sync_scenario_result("SYN-43", "SA3", "FAIL", result=fail_result)
    assert r["issue"] == "SYN-51" and r["status"] == "FAIL"
    fail_comment = next(c for c in calls["comments"] if c[0] == "SYN-51")
    assert fail_comment[2] is not None  # JSON detail attached on FAIL
    assert '"violations": 18' in fail_comment[2]
    assert '"file": "a.sql"' in fail_comment[2]
    transitioned = dict(calls["transitions"])
    assert transitioned["SYN-50"] == "Done"
    assert transitioned["SYN-51"] == "In Progress"
    # N1 untouched (we didn't sync it)
    assert "SYN-52" not in transitioned


def test_sync_scenario_result_no_ticket_is_noop(monkeypatch):
    _stub_jira(monkeypatch, [])
    r = jira_toolset.sync_scenario_result("", "CS1", "PASS")
    assert r["updated"] is False and r["reason"] == "no ticket supplied"


def test_sync_scenario_result_unknown_scenario(monkeypatch):
    _stub_jira(monkeypatch, [
        {"key": "SYN-50", "fields": {"summary": "CS1 - dbt model naming convention"}},
    ])
    r = jira_toolset.sync_scenario_result("SYN-43", "CS9", "PASS")
    assert r["updated"] is False and r["reason"] == "no matching subtask"


# --- Per-ticket BDD features must route only to Functional/Integration --------

def test_domain_for_ticket_only_functional_or_integration():
    """Per-ticket features must never land in CS/SA/NonFunctional folders, which
    the Functional & Integration stage drops (so they'd never run/sync)."""
    # A coding-standards-sounding ticket (like SYN-104) must NOT route to a
    # folder the BDD stage deletes.
    assert gherkin.domain_for_ticket(
        "SYN-104", "Enforce dbt model naming conventions and manifest lint standards"
    ) == "Functional"
    assert gherkin.domain_for_ticket("SYN-1", "BigQuery to Neo4j ingest pipeline") == "Integration"
    for summ in ["performance latency SLA", "security reliability posture",
                 "static secret scan", "validate enriched data"]:
        assert gherkin.domain_for_ticket("SYN-X", summ) in ("Functional", "Integration")


# --- Scenario-suite mapping must use the agent's registry keys ----------------

def test_bullet_to_gwt_emits_valid_suite_names():
    """Every generated step must name a suite the agent runner knows, else the
    BDD step fails with "Unknown suite '<name>'"."""
    valid = {"static", "standards", "integration", "functional", "nonfunctional"}
    bullets = [
        "p99 latency SLA under 5s",
        "system must be reliable; logging of failures",
        "rows ingested into neo4j graph",
        "raw rows become enriched",
        "sql passes sqlfluff lint",
        "no hardcoded secret credential",
        "every model has a primary key and unique test",
        "validate customer enriched completeness",
        # The SYN-133 AC2 that regressed: 'logged' must NOT route to nonfunctional.
        "a customer record has a null email field, then the row should be excluded from the mart and logged in processed_files_metadata",
    ]
    for b in bullets:
        steps = gherkin._bullet_to_gwt(b)
        suite = steps[0].split('"')[1]
        assert suite in valid, f"invalid suite {suite!r} for bullet {b!r}"


def test_bullet_to_gwt_logged_stays_functional():
    steps = gherkin._bullet_to_gwt(
        "the row should be excluded from the mart and logged in processed_files_metadata")
    assert steps[0] == 'Given the test suite is "functional"'
