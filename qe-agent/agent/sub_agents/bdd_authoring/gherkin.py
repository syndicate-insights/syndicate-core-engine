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
    """Pick the BDD feature folder for a per-ticket acceptance test.

    Per-ticket AC scenarios only run in the **Functional & Integration** BDD
    stage, so this returns just those two folders: ``Integration`` when the
    ticket is clearly integration-related, otherwise ``Functional``.

    Coding Standards, Static Analysis and Non-Functional are validated by the
    fixed non-BDD check subtasks (run via the agent's /qe/scenario API), not by
    per-ticket feature files — routing a feature into one of those folders would
    place it where the Functional & Integration stage drops it, so its scenarios
    would never run and never sync back to their subtasks.
    """
    s = (summary or "").lower()
    if any(k in s for k in ("integration", "ingest", "pipeline", "neo4j", "bigquery", "gcs")):
        return "Integration"
    return "Functional"


def feature_for_ticket(ticket: str, summary: str, bullets: list[str],
                       test_keys: list[str] | None = None) -> str:
    """Render a complete .feature file from acceptance-criteria bullets.

    When ``test_keys`` is supplied, each scenario is additionally tagged with the
    Jira ``Test`` subtask key that backs it (e.g. ``@PROJ-123``). The BDD results
    sync uses that tag to push each scenario's PASS/FAIL to the exact subtask
    instead of inferring the mapping from the ``ACn`` index. The list is aligned
    by position with ``bullets`` (scenario ``ACn`` -> ``test_keys[n-1]``).
    """
    title = (summary or ticket).strip()
    feature = [
        f"Feature: {title} ({ticket})",
        f"  Source: Jira ticket {ticket}",
        "",
        "  Background:",
        "    Given the QE Quality Agent is reachable",
        "",
    ]
    keys = test_keys or []
    for idx, bullet in enumerate(bullets or [f"acceptance criterion {ticket}-{1}"], start=1):
        gwt = _bullet_to_gwt(bullet)
        tags = f"{_SCENARIO_TAG} @{ticket}"
        test_key = keys[idx - 1] if idx - 1 < len(keys) else None
        if test_key:
            tags += f" @{test_key}"
        feature.append(f"  {tags}")
        feature.append(f"  Scenario: AC{idx} - {bullet[:80].rstrip()}")
        for line in gwt:
            feature.append(f"    {line}")
        feature.append("")
    return "\n".join(feature).rstrip() + "\n"


def _gwt(suite: str, scenario_id: str) -> list[str]:
    return [
        f'Given the test suite is "{suite}"',
        f'When I run scenario "{scenario_id}"',
        "Then the scenario status should be PASS",
    ]


def _has(text: str, *words: str) -> bool:
    """Whole-word keyword match so e.g. 'logged' does not match 'log'."""
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _bullet_to_gwt(bullet: str) -> list[str]:
    """Map a natural-language AC bullet to a deterministic scenario id.

    Suite names MUST match the agent's runner registry keys
    (static, standards, integration, functional, nonfunctional) — otherwise the
    BDD step fails with "Unknown suite '<name>'".
    """
    text = bullet.strip().lower()

    # 1. Non-functional (performance / reliability). Whole-word matching avoids
    #    false hits like 'logged' (a functional data-recording AC) -> 'log'.
    if _has(text, "sla", "latency", "performance", "throughput", "timing"):
        return _gwt("nonfunctional", "N1")
    if _has(text, "reliability", "security", "failure", "fault", "logging"):
        return _gwt("nonfunctional", "N2")

    # 2. Integration (GCS / BigQuery / Neo4j)
    if _has(text, "neo4j"):
        return _gwt("integration", "I3")
    if _has(text, "gcs"):
        return _gwt("integration", "I1")
    if _has(text, "raw") and _has(text, "enriched"):
        return _gwt("integration", "I2")

    # 3. Static analysis / coding standards
    if _has(text, "lint", "sqlfluff", "style"):
        return _gwt("static", "SA1")
    if _has(text, "secret", "hardcoded", "credential"):
        return _gwt("static", "SA5")
    if _has(text, "pk", "unique") or "primary key" in text:
        return _gwt("standards", "CS2")

    # Default: functional suite
    return _gwt("functional", "F5")
