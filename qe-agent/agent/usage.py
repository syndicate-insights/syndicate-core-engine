"""Process-wide token / quota usage tracking for the QE Quality Agent.

Every Gemini call the agent makes flows through the ``_after_model`` observability
callback, which already receives the ``usage_metadata`` (prompt / candidate /
total token counts) that Vertex AI returns. This module accumulates those counts
so the running process can answer a simple question at any time:

    "How many tokens has our implementation used, and how close are we to quota?"

The tracker is a lightweight, thread-safe, in-memory singleton (``TRACKER``).
It intentionally has no external dependencies so it imports cleanly in the
minimal CI image, and it is reset when the pod restarts — it measures usage for
the life of the running agent, which is exactly what the demo / cost slides
want to show. Persisting to BigQuery / Cloud Monitoring can layer on later.

Environment toggles:
  QE_TOKEN_QUOTA : soft monthly token budget used to compute the ``quota``
                   remaining / percent_used view (default 0 = "no quota set").
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int(value: object) -> int:
    """Coerce a possibly-missing / non-numeric token count to a safe int."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


@dataclass
class _Counter:
    """Aggregated token counts for one slice (a model, or a calling agent)."""

    calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, prompt: int, output: int, total: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.output_tokens += output
        # Prefer the provider's own total when present; otherwise derive it.
        self.total_tokens += total or (prompt + output)

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class TokenUsageTracker:
    """Thread-safe accumulator of Gemini token usage for this process."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _started_monotonic: float = field(default_factory=time.monotonic, repr=False)
    started_at: str = field(default_factory=_now_iso)
    last_updated: str | None = None
    total: _Counter = field(default_factory=_Counter)
    by_model: dict[str, _Counter] = field(default_factory=dict)
    by_source: dict[str, _Counter] = field(default_factory=dict)

    def record(self, *, model: str, source: str, prompt_tokens: object,
               output_tokens: object, total_tokens: object) -> None:
        """Record the token usage of a single model response.

        Called from the observability ``_after_model`` callback. Missing or
        non-numeric counts are treated as zero so tracking never breaks a run.
        """
        prompt = _int(prompt_tokens)
        output = _int(output_tokens)
        total = _int(total_tokens)
        model = model or "unknown"
        source = source or "unknown"
        with self._lock:
            self.total.add(prompt, output, total)
            self.by_model.setdefault(model, _Counter()).add(prompt, output, total)
            self.by_source.setdefault(source, _Counter()).add(prompt, output, total)
            self.last_updated = _now_iso()

    @staticmethod
    def _quota() -> int:
        try:
            return int(os.environ.get("QE_TOKEN_QUOTA", "0"))
        except ValueError:
            return 0

    def snapshot(self) -> dict:
        """Full token-usage JSON payload (safe to serialise straight to a client)."""
        with self._lock:
            uptime = max(0.0, time.monotonic() - self._started_monotonic)
            return {
                "status": "ok",
                "model_default": os.environ.get("QE_AGENT_MODEL", "gemini-2.5-flash"),
                "started_at": self.started_at,
                "last_updated": self.last_updated,
                "uptime_seconds": round(uptime, 1),
                "totals": self.total.as_dict(),
                "by_model": {m: c.as_dict() for m, c in self.by_model.items()},
                "by_source": {s: c.as_dict() for s, c in self.by_source.items()},
                "quota": self._quota_view(self.total.total_tokens),
            }

    def quota(self) -> dict:
        """Just the quota view (used vs limit) for the dedicated quota endpoint."""
        with self._lock:
            return {
                "status": "ok",
                "last_updated": self.last_updated,
                **self._quota_view(self.total.total_tokens),
            }

    def _quota_view(self, used: int) -> dict:
        limit = self._quota()
        view = {
            "total_tokens_used": used,
            "quota_limit": limit,
            "quota_configured": limit > 0,
        }
        if limit > 0:
            remaining = max(0, limit - used)
            view["remaining_tokens"] = remaining
            view["percent_used"] = round(used / limit * 100, 2)
            view["exceeded"] = used > limit
        return view

    def reset(self) -> dict:
        """Clear all counters (handy to isolate a single demo run)."""
        with self._lock:
            self.total = _Counter()
            self.by_model = {}
            self.by_source = {}
            self._started_monotonic = time.monotonic()
            self.started_at = _now_iso()
            self.last_updated = None
            return {"status": "ok", "reset_at": self.started_at}


# Process-wide singleton the observability callback writes to and the API reads.
TRACKER = TokenUsageTracker()
