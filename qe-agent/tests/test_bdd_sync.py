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
    # No test key and no generated check -> parent tag plus @manual.
    assert "@JiraGenerated @SYN-99 @manual" in feat
    assert "@SYN-101" not in feat


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


# --- Generated-check embedding (agentic authoring) ----------------------------

def test_feature_embeds_generated_bq_check():
    """A generated bq_query check is embedded as an inline BigQuery-check step."""
    check = {
        "kind": "bq_query",
        "table": "customer_enriched",
        "sql": "SELECT COUNTIF(phone_number != REGEXP_REPLACE(phone, r'[^0-9]','')) AS violations\nFROM `p.d.customer_enriched`",
        "assert": {"column": "violations", "equals": 0},
    }
    feat = gherkin.feature_for_ticket(
        "SYN-300", "phone check", ["phone_number must be digits-only"],
        test_keys=["SYN-301"], checks=[check],
    )
    assert "When I run the BigQuery check:" in feat
    assert "COUNTIF(phone_number" in feat
    assert 'Then the result column "violations" should be 0' in feat
    assert "@manual" not in feat


def test_feature_marks_ungenerable_ac_manual():
    """An AC with no generated check becomes a @manual, non-passing scenario."""
    feat = gherkin.feature_for_ticket(
        "SYN-300", "x", ["something unverifiable"],
        test_keys=["SYN-301"], checks=[None],
    )
    assert "@manual" in feat
    assert "this scenario requires manual verification" in feat
    assert "BigQuery check" not in feat


def test_generate_check_validates_and_returns_spec(monkeypatch):
    from agent.sub_agents.bdd_authoring import test_generator as tg
    spec = {"kind": "bq_query", "table": "customer_enriched",
            "sql": "SELECT COUNTIF(1=2) AS violations FROM `p.d.customer_enriched`",
            "assert": {"column": "violations", "equals": 0}}
    monkeypatch.setattr(tg.bq, "table_schema",
                        lambda t: {"table": f"p.d.{t}", "columns": [{"name": "phone", "type": "STRING"}]})
    monkeypatch.setattr(tg, "_llm_generate", lambda prompt: __import__("json").dumps(spec))
    monkeypatch.setattr(tg.bq, "dry_run_query", lambda sql: {"ok": True, "bytes": 10})
    out = tg.generate_check("SYN-300", "phone_number must be digits-only")
    assert out and out["assert"]["column"] == "violations"


def test_generate_check_skips_when_dry_run_fails(monkeypatch):
    from agent.sub_agents.bdd_authoring import test_generator as tg
    spec = {"kind": "bq_query", "sql": "SELECT bad FROM nope",
            "assert": {"column": "violations", "equals": 0}}
    monkeypatch.setattr(tg.bq, "table_schema",
                        lambda t: {"table": f"p.d.{t}", "columns": [{"name": "x", "type": "STRING"}]})
    monkeypatch.setattr(tg, "_llm_generate", lambda prompt: __import__("json").dumps(spec))
    monkeypatch.setattr(tg.bq, "dry_run_query", lambda sql: {"ok": False, "error": "no such column"})
    assert tg.generate_check("SYN-300", "anything") is None


# --- Authoring idempotency (no duplicate subtasks/PRs on webhook retries) -----

def test_already_authored_detects_prior_bdd_subtasks(monkeypatch):
    monkeypatch.setattr(jira_toolset, "_find_test_subtasks",
                        lambda t: [{"key": "SYN-134", "fields": {"summary": "BDD AC1 for SYN-133: x"}}])
    assert jira_toolset.already_authored("SYN-133") is True


def test_already_authored_false_without_bdd_subtasks(monkeypatch):
    monkeypatch.setattr(jira_toolset, "_find_test_subtasks",
                        lambda t: [{"key": "SYN-200", "fields": {"summary": "A human-made task"}}])
    assert jira_toolset.already_authored("SYN-133") is False
    monkeypatch.setattr(jira_toolset, "_find_test_subtasks", lambda t: [])
    assert jira_toolset.already_authored("SYN-133") is False


def test_generate_check_retries_transient_429(monkeypatch):
    from agent.sub_agents.bdd_authoring import test_generator as tg
    import json as _json
    monkeypatch.setattr(tg.bq, "table_schema",
                        lambda t: {"table": f"p.d.{t}", "columns": [{"name": "phone", "type": "STRING"}]})
    monkeypatch.setattr(tg.bq, "dry_run_query", lambda sql: {"ok": True, "bytes": 10})
    sleeps = []
    monkeypatch.setattr(tg.time, "sleep", lambda s: sleeps.append(s))
    good = _json.dumps({"kind": "bq_query", "table": "customer_enriched",
                        "sql": "SELECT COUNTIF(1=2) AS violations FROM `p.d.customer_enriched`",
                        "assert": {"column": "violations", "equals": 0}})
    seq = iter([Exception("429 RESOURCE_EXHAUSTED"), Exception("429 Too Many Requests"), good])

    def fake(prompt):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(tg, "_llm_generate", fake)
    out = tg.generate_check("SYN-1", "phone digits")
    assert out and out["assert"]["column"] == "violations"
    assert sleeps == [5, 5]  # retried twice, 5s apart


def test_generate_check_no_retry_on_permanent_error(monkeypatch):
    from agent.sub_agents.bdd_authoring import test_generator as tg
    monkeypatch.setattr(tg.bq, "table_schema",
                        lambda t: {"table": f"p.d.{t}", "columns": [{"name": "x", "type": "STRING"}]})
    sleeps = []
    monkeypatch.setattr(tg.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(tg, "_llm_generate",
                        lambda p: (_ for _ in ()).throw(Exception("400 INVALID_ARGUMENT")))
    assert tg.generate_check("SYN-2", "x") is None
    assert sleeps == []  # permanent error: no retry


# --- Neo4j / Cypher check generation ------------------------------------------

def test_feature_embeds_generated_cypher_check():
    check = {
        "kind": "cypher",
        "cypher": "MATCH (a:Account) WHERE NOT (:Customer)-[:HAS_ACCOUNT]->(a)\nRETURN count(a) AS violations",
        "assert": {"column": "violations", "equals": 0},
    }
    feat = gherkin.feature_for_ticket(
        "SYN-251", "graph", ["Every Account node must link to a Customer"],
        test_keys=["SYN-254"], checks=[check],
    )
    assert "When I run the Neo4j check:" in feat
    assert "HAS_ACCOUNT" in feat
    assert 'Then the result column "violations" should be 0' in feat
    assert "@manual" not in feat


def test_generate_check_routes_graph_ac_to_cypher(monkeypatch):
    from agent.sub_agents.bdd_authoring import test_generator as tg
    import json as _json
    monkeypatch.setattr(tg.neo, "graph_schema",
                        lambda: {"labels": ["Customer", "Account"], "relationships": ["HAS_ACCOUNT"]})
    monkeypatch.setattr(tg.neo, "explain", lambda q: {"ok": True})
    spec = _json.dumps({"kind": "cypher",
                        "cypher": "MATCH (a:Account) WHERE NOT (:Customer)-[:HAS_ACCOUNT]->(a) RETURN count(a) AS violations",
                        "assert": {"column": "violations", "equals": 0}})
    monkeypatch.setattr(tg, "_llm_generate", lambda p: spec)
    out = tg.generate_check("SYN-251", "When the Neo4j graph is queried, every Account links to a Customer")
    assert out and out["kind"] == "cypher" and "MATCH" in out["cypher"]


def test_generate_check_routes_data_ac_to_bq(monkeypatch):
    from agent.sub_agents.bdd_authoring import test_generator as tg
    import json as _json
    monkeypatch.setattr(tg.bq, "table_schema",
                        lambda t: {"table": f"p.d.{t}", "columns": [{"name": "customer_id", "type": "STRING"}]})
    monkeypatch.setattr(tg.bq, "dry_run_query", lambda sql: {"ok": True, "bytes": 5})
    spec = _json.dumps({"kind": "bq_query", "table": "customer_enriched",
                        "sql": "SELECT COUNTIF(customer_id IS NULL) AS violations FROM `p.d.customer_enriched`",
                        "assert": {"column": "violations", "equals": 0}})
    monkeypatch.setattr(tg, "_llm_generate", lambda p: spec)
    out = tg.generate_check("SYN-251", "customer_enriched must have no null customer_id")
    assert out and out["kind"] == "bq_query"


# --- Cross-system (BigQuery vs Neo4j) checks ----------------------------------

def test_feature_embeds_cross_check_three_steps():
    check = {
        "kind": "cross_check",
        "bq_sql": "SELECT COUNT(DISTINCT customer_id) AS value FROM `p.d.customer_enriched`",
        "cypher": "MATCH (c:Customer) RETURN count(c) AS value",
        "compare": "eq",
    }
    feat = gherkin.feature_for_ticket(
        "SYN-267", "x", ["Neo4j Customer count equals distinct customer_id in customer_enriched"],
        test_keys=["SYN-270"], checks=[check],
    )
    assert "When I capture the BigQuery value:" in feat
    assert "And I capture the Neo4j value:" in feat
    assert "Then the BigQuery and Neo4j values should be equal" in feat
    assert "COUNT(DISTINCT customer_id)" in feat and "MATCH (c:Customer)" in feat
    assert "@manual" not in feat


def test_generate_check_routes_cross_system_ac(monkeypatch):
    from agent.sub_agents.bdd_authoring import test_generator as tg
    import json as _json
    monkeypatch.setattr(tg.bq, "table_schema",
                        lambda t: {"table": f"p.d.{t}", "columns": [{"name": "customer_id", "type": "STRING"}]})
    monkeypatch.setattr(tg.bq, "dry_run_query", lambda sql: {"ok": True, "bytes": 1})
    monkeypatch.setattr(tg.neo, "graph_schema",
                        lambda: {"labels": ["Customer"], "relationships": ["HAS_ACCOUNT"]})
    monkeypatch.setattr(tg.neo, "explain", lambda q: {"ok": True})
    spec = _json.dumps({"kind": "cross_check",
                        "bq_sql": "SELECT COUNT(DISTINCT customer_id) AS value FROM `p.d.customer_enriched`",
                        "cypher": "MATCH (c:Customer) RETURN count(c) AS value", "compare": "eq"})
    monkeypatch.setattr(tg, "_llm_generate", lambda p: spec)
    out = tg.generate_check(
        "SYN-267", "number of Customer nodes must equal distinct customer_id in customer_enriched")
    assert out and out["kind"] == "cross_check" and "bq_sql" in out and "cypher" in out


def test_is_cross_system_ac_detection():
    from agent.sub_agents.bdd_authoring import test_generator as tg
    assert tg._is_cross_system_ac("Customer nodes must equal distinct customer_id in customer_enriched")
    assert not tg._is_cross_system_ac("every Account node links to a Customer")
    assert not tg._is_cross_system_ac("customer_enriched has no null customer_id")


# --- AC bullet parsing: wrapped lines must be joined, not truncated -----------

def test_extract_bullets_joins_wrapped_continuation_lines():
    from agent.tools import jira_toolset
    desc = (
        "As a data consumer, I want ... reliable.\n\n"
        "  Acceptance Criteria:\n\n"
        "* (dbt) When address_enriched is built, Then full_address must equal line1, city,\n"
        "postcode and country joined by commas for every row.\n"
        "* (BigQuery) Then there must be zero rows where\n"
        "customer_id is null.\n"
    )
    bullets = jira_toolset._extract_bullets(desc)
    assert len(bullets) == 2
    # The wrapped continuation must be present (was previously truncated).
    assert "postcode and country joined by commas" in bullets[0]
    assert "customer_id is null" in bullets[1]
