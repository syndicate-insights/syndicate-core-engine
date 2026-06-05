"""Suite registry — single entry point for running scenarios.

Each suite module exposes a `REGISTRY` (scenario_id -> callable) and a
`run_all()`. This module lets the CLI / API / sub-agents address suites and
individual scenarios by name and aggregate pass/fail deterministically.
"""

from __future__ import annotations

from agent.results import ScenarioResult, Status
from agent.scenarios import (
    coding_standards,
    functional,
    integration,
    non_functional,
    static_analysis,
)

SUITES = {
    "static": static_analysis,
    "standards": coding_standards,
    "integration": integration,
    "functional": functional,
    "nonfunctional": non_functional,
}


def list_scenarios() -> dict[str, list[str]]:
    return {name: list(mod.REGISTRY.keys()) for name, mod in SUITES.items()}


def run_scenario(suite: str, scenario_id: str) -> dict:
    mod = SUITES.get(suite)
    if mod is None:
        return ScenarioResult(scenario_id, suite, "unknown suite", Status.ERROR,
                              findings=[f"Unknown suite '{suite}'."]).to_dict()
    fn = mod.REGISTRY.get(scenario_id)
    if fn is None:
        return ScenarioResult(scenario_id, suite, "unknown scenario", Status.ERROR,
                              findings=[f"Unknown scenario '{scenario_id}' in suite '{suite}'."]).to_dict()
    try:
        return fn().to_dict()
    except Exception as exc:  # noqa: BLE001
        return ScenarioResult(scenario_id, suite, fn.__name__, Status.ERROR, findings=[str(exc)]).to_dict()


def run_suite(suite: str) -> dict:
    mod = SUITES.get(suite)
    if mod is None:
        return {"suite": suite, "error": f"Unknown suite '{suite}'.", "passed": False, "results": []}
    results = mod.run_all()
    passed = all(r["status"] == Status.PASS.value for r in results)
    return {
        "suite": suite,
        "passed": passed,
        "total": len(results),
        "failures": sum(1 for r in results if r["status"] != Status.PASS.value),
        "results": results,
    }
