"""Unit tests for the in-memory token / quota usage tracker.

These are fully network-free: they drive `TokenUsageTracker` directly and assert
the JSON payload the `/qe/usage/*` endpoints return.
"""

from __future__ import annotations

from agent.usage import TokenUsageTracker


def test_records_and_aggregates_across_models_and_sources():
    t = TokenUsageTracker()
    t.record(model="gemini-2.5-flash", source="root_agent",
             prompt_tokens=100, output_tokens=20, total_tokens=120)
    t.record(model="gemini-2.5-flash", source="bdd_authoring",
             prompt_tokens=50, output_tokens=10, total_tokens=60)

    snap = t.snapshot()
    assert snap["totals"] == {
        "calls": 2, "prompt_tokens": 150, "output_tokens": 30, "total_tokens": 180,
    }
    assert snap["by_model"]["gemini-2.5-flash"]["total_tokens"] == 180
    assert snap["by_source"]["root_agent"]["calls"] == 1
    assert snap["by_source"]["bdd_authoring"]["prompt_tokens"] == 50
    assert snap["last_updated"] is not None


def test_missing_total_is_derived_from_prompt_plus_output():
    t = TokenUsageTracker()
    t.record(model="m", source="s", prompt_tokens=7, output_tokens=3, total_tokens=None)
    assert t.snapshot()["totals"]["total_tokens"] == 10


def test_non_numeric_counts_are_treated_as_zero():
    t = TokenUsageTracker()
    t.record(model="m", source="s", prompt_tokens="?", output_tokens=None, total_tokens="?")
    totals = t.snapshot()["totals"]
    assert totals["calls"] == 1
    assert totals["total_tokens"] == 0


def test_quota_view_when_configured(monkeypatch):
    monkeypatch.setenv("QE_TOKEN_QUOTA", "1000")
    t = TokenUsageTracker()
    t.record(model="m", source="s", prompt_tokens=200, output_tokens=50, total_tokens=250)

    quota = t.quota()
    assert quota["total_tokens_used"] == 250
    assert quota["quota_limit"] == 1000
    assert quota["remaining_tokens"] == 750
    assert quota["percent_used"] == 25.0
    assert quota["exceeded"] is False


def test_quota_marks_exceeded(monkeypatch):
    monkeypatch.setenv("QE_TOKEN_QUOTA", "100")
    t = TokenUsageTracker()
    t.record(model="m", source="s", prompt_tokens=90, output_tokens=30, total_tokens=120)
    quota = t.quota()
    assert quota["exceeded"] is True
    assert quota["remaining_tokens"] == 0


def test_quota_absent_when_not_configured(monkeypatch):
    monkeypatch.delenv("QE_TOKEN_QUOTA", raising=False)
    t = TokenUsageTracker()
    t.record(model="m", source="s", prompt_tokens=5, output_tokens=5, total_tokens=10)
    quota = t.quota()
    assert quota["quota_configured"] is False
    assert "remaining_tokens" not in quota


def test_reset_clears_counters():
    t = TokenUsageTracker()
    t.record(model="m", source="s", prompt_tokens=5, output_tokens=5, total_tokens=10)
    t.reset()
    snap = t.snapshot()
    assert snap["totals"]["calls"] == 0
    assert snap["totals"]["total_tokens"] == 0
    assert snap["by_model"] == {}
    assert snap["last_updated"] is None
