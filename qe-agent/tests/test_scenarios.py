"""Unit tests for the deterministic suites that don't require GCP/Neo4j/K8s.

These cover static analysis and coding standards (filesystem-only checks) plus
the suite runner wiring. Cloud-dependent suites are exercised in integration
environments, not here.
"""

from __future__ import annotations

import os

os.environ.setdefault("REPO_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.scenarios import coding_standards, static_analysis  # noqa: E402


def test_static_registry_has_five():
    assert set(static_analysis.REGISTRY) == {"SA1", "SA2", "SA3", "SA4", "SA5"}


def test_standards_registry_has_five():
    assert set(coding_standards.REGISTRY) == {"CS1", "CS2", "CS3", "CS4", "CS5"}


def test_secret_scan_runs_and_is_serialisable():
    result = static_analysis.s_secret_scan().to_dict()
    assert result["scenario_id"] == "SA5"
    assert result["status"] in {"PASS", "FAIL", "ERROR"}


def test_dbt_naming_runs():
    result = coding_standards.c_dbt_naming().to_dict()
    assert result["scenario_id"] == "CS1"
    assert "violations" in result["metrics"]
