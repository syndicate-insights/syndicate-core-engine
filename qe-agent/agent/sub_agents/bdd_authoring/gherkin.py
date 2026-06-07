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
    """Parse a Given/When/Then bullet, otherwise build a sensible default."""
    text = bullet.strip().rstrip(".")
    lower = text.lower()
    # Pre-formatted Gherkin in the bullet — keep as is.
    m = re.match(r"^(given|when|then|and|but)\s+(.*)", lower, re.I)
    if m:
        keyword = m.group(1).capitalize()
        return [f'{keyword} {text[len(m.group(1)):].strip()}']
    # "Given X, When Y, Then Z" — inline form.
    inline = re.findall(r"(given|when|then|and|but)\s+(.+?)(?=,?\s+(?:given|when|then|and|but)\b|$)",
                        text, flags=re.I)
    if inline and len(inline) >= 2:
        return [f"{kw.capitalize()} {body.strip().rstrip(',')}" for kw, body in inline]
    # Default: generic GWT pointing at a manual acceptance step.
    return [
        'Given the test suite is "functional"',
        f'When I validate the acceptance criterion "{text}"',
        'Then the scenario status should be PASS',
    ]
