"""Gherkin generation from Jira acceptance criteria.

Heuristic, deterministic translator: turns each acceptance-criteria bullet into
a `Scenario` with reasonable `Given/When/Then` steps. The sub-agent's LLM is
free to refine the output before it's persisted; this module guarantees we
always have a valid feature file to start from.
"""

from __future__ import annotations

import re

_SCENARIO_TAG = "@JiraGenerated"


def slugify(value: str, max_len: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return (s[:max_len] or "scenario").lower()


def domain_for_ticket(ticket: str, summary: str) -> str:
    """Pick a folder under bdd-tests/.../feature/ based on summary heuristics."""
    s = (summary or "").lower()
    if any(k in s for k in ("performance", "latency", "sla", "security", "reliability")):
        return "NonFunctional"
    if any(k in s for k in ("integration", "ingest", "pipeline", "neo4j", "bigquery", "gcs")):
        return "Integration"
    if any(k in s for k in ("standard", "naming", "convention", "manifest", "lint")):
        return "CodingStandards"
    if any(k in s for k in ("static", "scan", "secret", "vulnerability")):
        return "StaticAnalysis"
    return "Functional"


def feature_for_ticket(ticket: str, summary: str, bullets: list[str]) -> str:
    """Render a complete .feature file from acceptance-criteria bullets."""
    title = (summary or ticket).strip()
    feature = [
        f"Feature: {title} ({ticket})",
        f"  Source: Jira ticket {ticket}",
        "",
        "  Background:",
        "    Given the QE Quality Agent is reachable",
        "",
    ]
    for idx, bullet in enumerate(bullets or [f"acceptance criterion {ticket}-{1}"], start=1):
        gwt = _bullet_to_gwt(bullet)
        feature.append(f"  {_SCENARIO_TAG} @{ticket}")
        feature.append(f"  Scenario: AC{idx} - {bullet[:80].rstrip()}")
        for line in gwt:
            feature.append(f"    {line}")
        feature.append("")
    return "\n".join(feature).rstrip() + "\n"


def _bullet_to_gwt(bullet: str) -> list[str]:
    """Map natural language bullets to the technical scenario ids the agent serves."""
    text = bullet.strip().lower()
    
    # 1. Non-Functional Mapping (Performance, SLA, Logs, etc.)
    if any(k in text for k in ("sla", "latency", "performance", "timing")):
        return ['Given the test suite is "non_functional"', 'When I run scenario "N1"', 'Then the scenario status should be PASS']
    if any(k in text for k in ("reliability", "security", "fail", "log")):
        return ['Given the test suite is "non_functional"', 'When I run scenario "N2"', 'Then the scenario status should be PASS']
    
    # 2. Integration Mapping (GCS, BQ, Neo4j)
    if "neo4j" in text:
        return ['Given the test suite is "integration"', 'When I run scenario "I3"', 'Then the scenario status should be PASS']
    if "gcs" in text:
        return ['Given the test suite is "integration"', 'When I run scenario "I1"', 'Then the scenario status should be PASS']
    if "raw" in text and "enriched" in text:
        return ['Given the test suite is "integration"', 'When I run scenario "I2"', 'Then the scenario status should be PASS']

    # 3. Static Analysis / Standards
    if any(k in text for k in ("lint", "sqlfluff", "style")):
        return ['Given the test suite is "static_analysis"', 'When I run scenario "SA1"', 'Then the scenario status should be PASS']
    if any(k in text for k in ("secret", "hardcoded", "credential")):
        return ['Given the test suite is "static_analysis"', 'When I run scenario "SA5"', 'Then the scenario status should be PASS']
    if any(k in text for k in ("pk", "primary key", "unique")):
        return ['Given the test suite is "coding_standards"', 'When I run scenario "CS2"', 'Then the scenario status should be PASS']

    # Default to Functional suite
    return [
        'Given the test suite is "functional"',
        'When I run scenario "F5"',
        'Then the scenario status should be PASS',
    ]
