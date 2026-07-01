"""Per-run token / quota usage tracking for the QE Quality Agent.

Every time the agent is activated for a ticket (the Jira webhook authors BDD
scenarios, or a failing Harness run is reconciled) it makes one or more Gemini
calls. This module attributes those token counts to the **run** they belong to
so we can answer, per activation:

    "How many tokens did processing SYN-482 cost, and did it stay under quota?"

A *run* is opened with :func:`TokenUsageTracker.start_run` (keyed by the Jira
ticket) and closed with :func:`end_run`. Any :func:`record` call in between —
whether from the direct ``genai`` call in ``test_generator`` or from the ADK
``_after_model`` callback — is attributed to the current run via a
``ContextVar`` (so concurrent runs don't bleed into each other). Model calls
that happen with no run open (e.g. the interactive ADK ``/run`` surface) fall
back to grouping by their ADK ``invocation_id``.

Quota is evaluated **per run**: ``QE_TOKEN_QUOTA`` is the token budget for a
single activation, not a lifetime cap. Runs are kept in a bounded in-memory
history (newest first); it resets when the pod restarts, which is the intended
"usage since this version went live" behaviour for the demo / cost story.

Environment toggles:
  QE_TOKEN_QUOTA    : per-run token budget for the quota view (default 0 = off).
  QE_USAGE_MAX_RUNS : how many recent runs to retain (default 100).
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

# The run the current execution context is attributed to. Set by start_run,
# cleared by end_run. A ContextVar (not a plain global) so parallel async runs
# each see their own current run.
_CURRENT_RUN: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "qe_current_run", default=None
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int(value: object) -> int:
    """Coerce a possibly-missing / non-numeric token count to a safe int."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


@dataclass
class RunUsage:
    """Token usage accumulated for a single agent activation (one run)."""

    run_id: str
    label: str | None = None  # human-friendly key, typically the Jira ticket
    started_at: str = field(default_factory=_now_iso)
    ended_at: str | None = None
    status: str = "active"  # "active" while open, "complete" once ended
    calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    by_model: dict[str, int] = field(default_factory=dict)

    def add(self, model: str, prompt: int, output: int, total: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.output_tokens += output
        # Prefer the provider's own total when present; otherwise derive it.
        resolved = total or (prompt + output)
        self.total_tokens += resolved
        self.by_model[model] = self.by_model.get(model, 0) + resolved

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "label": self.label,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "by_model": dict(self.by_model),
            "quota": _quota_view(self.total_tokens),
        }


def _quota_view(used: int) -> dict:
    """Per-run quota view: usage vs the QE_TOKEN_QUOTA per-run budget."""
    try:
        limit = int(os.environ.get("QE_TOKEN_QUOTA", "0"))
    except ValueError:
        limit = 0
    view = {
        "total_tokens_used": used,
        "quota_limit": limit,
        "quota_configured": limit > 0,
        "scope": "per_run",
    }
    if limit > 0:
        view["remaining_tokens"] = max(0, limit - used)
        view["percent_used"] = round(used / limit * 100, 2)
        view["exceeded"] = used > limit
    return view


@dataclass
class TokenUsageTracker:
    """Thread-safe, run-scoped accumulator of Gemini token usage."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    started_at: str = field(default_factory=_now_iso)
    _runs: "OrderedDict[str, RunUsage]" = field(default_factory=OrderedDict, repr=False)
    _last_run_id: str | None = None

    @staticmethod
    def _max_runs() -> int:
        try:
            return max(1, int(os.environ.get("QE_USAGE_MAX_RUNS", "100")))
        except ValueError:
            return 100

    def start_run(self, run_id: str | None = None, label: str | None = None) -> str:
        """Open a new run and make it the current one for this context.

        ``run_id`` defaults to a generated id; pass the Jira ticket to make the
        run addressable as ``/qe/usage/runs/<ticket>``. Returns the run id.
        """
        rid = run_id or f"run-{uuid.uuid4().hex[:12]}"
        with self._lock:
            # Re-opening an existing ticket run reuses it (webhook can retry).
            run = self._runs.get(rid)
            if run is None:
                run = RunUsage(run_id=rid, label=label or run_id)
                self._runs[rid] = run
                self._evict_locked()
            else:
                run.status = "active"
                run.ended_at = None
            self._last_run_id = rid
        _CURRENT_RUN.set(rid)
        return rid

    def end_run(self, run_id: str | None = None) -> None:
        """Close the current (or given) run and detach it from this context."""
        rid = run_id or _CURRENT_RUN.get()
        if rid is not None:
            with self._lock:
                run = self._runs.get(rid)
                if run is not None and run.status == "active":
                    run.status = "complete"
                    run.ended_at = _now_iso()
        _CURRENT_RUN.set(None)

    def record(self, *, model: str, source: str = "", invocation_id: str | None = None,
               prompt_tokens: object, output_tokens: object, total_tokens: object) -> None:
        """Attribute one model response's token usage to the current run.

        With no run open, groups the call under its ADK ``invocation_id`` (or a
        generated id) so ad-hoc / interactive calls are still per-run. Never
        raises — usage tracking must not break an agent run.
        """
        prompt, output, total = _int(prompt_tokens), _int(output_tokens), _int(total_tokens)
        model = model or "unknown"
        rid = _CURRENT_RUN.get() or invocation_id
        with self._lock:
            if rid is None or rid not in self._runs:
                if rid is None:
                    rid = f"run-{uuid.uuid4().hex[:12]}"
                if rid not in self._runs:
                    self._runs[rid] = RunUsage(run_id=rid, label=source or rid)
                    self._evict_locked()
            self._runs[rid].add(model, prompt, output, total)
            self._last_run_id = rid

    def _evict_locked(self) -> None:
        """Drop the oldest runs once history exceeds the cap (lock held)."""
        cap = self._max_runs()
        while len(self._runs) > cap:
            self._runs.popitem(last=False)

    def run(self, run_id: str) -> dict | None:
        with self._lock:
            run = self._runs.get(run_id)
            return run.as_dict() if run is not None else None

    def current(self) -> dict | None:
        """The most recently active run — what ``/qe/usage/quota`` reports on."""
        with self._lock:
            if self._last_run_id is None:
                return None
            run = self._runs.get(self._last_run_id)
            return run.as_dict() if run is not None else None

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            runs = list(self._runs.values())[-limit:]
        return [r.as_dict() for r in reversed(runs)]

    def snapshot(self) -> dict:
        """Full payload: the current run, recent run history, and process meta."""
        current = self.current()
        return {
            "status": "ok",
            "scope": "per_run",
            "model_default": os.environ.get("QE_AGENT_MODEL", "gemini-2.5-flash"),
            "process_started_at": self.started_at,
            "current_run": current,
            "recent_runs": self.recent(),
        }

    def reset(self) -> dict:
        with self._lock:
            self._runs.clear()
            self._last_run_id = None
            self.started_at = _now_iso()
        _CURRENT_RUN.set(None)
        return {"status": "ok", "reset_at": self.started_at}


# Process-wide singleton the callbacks / tools write to and the API reads.
TRACKER = TokenUsageTracker()


@contextlib.contextmanager
def track_run(run_id: str | None = None, label: str | None = None):
    """Open a tracked run for the duration of a ``with`` block.

    Every ``TRACKER.record`` inside the block is attributed to this run; the run
    is closed even if the block raises. Yields the run id.
    """
    rid = TRACKER.start_run(run_id, label=label)
    try:
        yield rid
    finally:
        TRACKER.end_run(rid)
