"""Unit tests for the per-run token / quota usage tracker.

Fully network-free: they drive `TokenUsageTracker` directly and assert the
per-run JSON the `/qe/usage/*` endpoints return. The isolated-logic tests use
their own tracker instance; the `track_run` context-manager test exercises the
module singleton (and resets it) since that is what the app wires in.
"""

from __future__ import annotations

from agent import usage as usage_mod
from agent.usage import TokenUsageTracker


def _run(t: TokenUsageTracker, run_id: str, calls: list[tuple[int, int, int | None]]):
    """Open a run on tracker `t`, record `calls`, close it."""
    t.start_run(run_id, label=run_id)
    for prompt, output, total in calls:
        t.record(model="gemini-2.5-flash", prompt_tokens=prompt,
                 output_tokens=output, total_tokens=total)
    t.end_run(run_id)


def test_usage_is_attributed_per_run():
    t = TokenUsageTracker()
    _run(t, "SYN-1", [(100, 20, 120), (50, 10, 60)])
    _run(t, "SYN-2", [(5, 5, 10)])

    run1, run2 = t.run("SYN-1"), t.run("SYN-2")
    # Each run only holds its own tokens — not a running total.
    assert run1["total_tokens"] == 180 and run1["calls"] == 2
    assert run2["total_tokens"] == 10 and run2["calls"] == 1
    assert run1["status"] == "complete" and run1["ended_at"] is not None


def test_current_reports_most_recent_run_only():
    t = TokenUsageTracker()
    _run(t, "SYN-10", [(100, 0, 100)])
    _run(t, "SYN-11", [(7, 3, 10)])

    current = t.current()
    assert current["run_id"] == "SYN-11"
    assert current["total_tokens"] == 10  # not 110


def test_records_with_no_open_run_group_by_invocation():
    t = TokenUsageTracker()
    t.record(model="m", invocation_id="inv-abc", prompt_tokens=4, output_tokens=6, total_tokens=10)
    t.record(model="m", invocation_id="inv-abc", prompt_tokens=1, output_tokens=1, total_tokens=2)
    assert t.run("inv-abc")["total_tokens"] == 12


def test_missing_total_is_derived_and_non_numeric_is_zero():
    t = TokenUsageTracker()
    _run(t, "SYN-20", [(7, 3, None)])
    t.start_run("SYN-20b", label="SYN-20b")
    t.record(model="m", prompt_tokens="?", output_tokens=None, total_tokens="?")
    t.end_run("SYN-20b")
    assert t.run("SYN-20")["total_tokens"] == 10       # derived from prompt+output
    assert t.run("SYN-20b")["total_tokens"] == 0       # non-numeric -> zero
    assert t.run("SYN-20b")["calls"] == 1


def test_per_run_quota_view(monkeypatch):
    monkeypatch.setenv("QE_TOKEN_QUOTA", "1000")
    t = TokenUsageTracker()
    _run(t, "SYN-30", [(200, 50, 250)])
    q = t.run("SYN-30")["quota"]
    assert q["scope"] == "per_run"
    assert q["total_tokens_used"] == 250
    assert q["remaining_tokens"] == 750
    assert q["percent_used"] == 25.0
    assert q["exceeded"] is False


def test_quota_is_per_run_not_cumulative(monkeypatch):
    monkeypatch.setenv("QE_TOKEN_QUOTA", "100")
    t = TokenUsageTracker()
    # Two separate runs, each under budget; quota must not accumulate across them.
    _run(t, "SYN-40", [(60, 20, 80)])
    _run(t, "SYN-41", [(60, 20, 80)])
    assert t.run("SYN-40")["quota"]["exceeded"] is False
    assert t.run("SYN-41")["quota"]["exceeded"] is False
    # A single run over budget is flagged.
    _run(t, "SYN-42", [(90, 30, 120)])
    assert t.run("SYN-42")["quota"]["exceeded"] is True
    assert t.run("SYN-42")["quota"]["remaining_tokens"] == 0


def test_quota_absent_when_not_configured(monkeypatch):
    monkeypatch.delenv("QE_TOKEN_QUOTA", raising=False)
    t = TokenUsageTracker()
    _run(t, "SYN-50", [(5, 5, 10)])
    q = t.run("SYN-50")["quota"]
    assert q["quota_configured"] is False
    assert "remaining_tokens" not in q


def test_history_is_bounded(monkeypatch):
    monkeypatch.setenv("QE_USAGE_MAX_RUNS", "3")
    t = TokenUsageTracker()
    for i in range(5):
        _run(t, f"SYN-{i}", [(1, 1, 2)])
    ids = [r["run_id"] for r in t.recent(50)]
    assert ids == ["SYN-4", "SYN-3", "SYN-2"]  # oldest two evicted, newest first
    assert t.run("SYN-0") is None


def test_reset_clears_runs():
    t = TokenUsageTracker()
    _run(t, "SYN-60", [(5, 5, 10)])
    t.reset()
    assert t.current() is None
    assert t.recent() == []


def test_track_run_context_manager_attributes_and_closes():
    """`track_run` (used by the authoring flow) attributes records to the run
    and closes it even on error. Exercises the module singleton."""
    usage_mod.TRACKER.reset()
    with usage_mod.track_run("SYN-70", label="SYN-70"):
        usage_mod.TRACKER.record(model="m", prompt_tokens=100, output_tokens=20, total_tokens=120)
    assert usage_mod.TRACKER.run("SYN-70")["total_tokens"] == 120
    assert usage_mod.TRACKER.run("SYN-70")["status"] == "complete"

    try:
        with usage_mod.track_run("SYN-71", label="SYN-71"):
            usage_mod.TRACKER.record(model="m", prompt_tokens=5, output_tokens=5, total_tokens=10)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    run = usage_mod.TRACKER.run("SYN-71")
    assert run["total_tokens"] == 10 and run["status"] == "complete"  # closed despite error
    usage_mod.TRACKER.reset()
