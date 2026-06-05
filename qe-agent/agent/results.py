"""Shared result model used by every scenario and toolset.

Scenarios are deterministic checks that return a `ScenarioResult`. The agent's
LLM layer only triages / summarises failures; pass-fail gating is decided by the
deterministic `status` field so Harness can rely on it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


@dataclass
class ScenarioResult:
    """Outcome of a single test scenario."""

    scenario_id: str
    suite: str
    title: str
    status: Status = Status.PASS
    expected: Any = None
    actual: Any = None
    findings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def fail(self, finding: str) -> "ScenarioResult":
        self.status = Status.FAIL
        self.findings.append(finding)
        return self

    def error(self, finding: str) -> "ScenarioResult":
        self.status = Status.ERROR
        self.findings.append(finding)
        return self

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d
