"""Gherkin generation from Jira acceptance criteria.

Each acceptance-criterion becomes a `Scenario`. The agent generates the **actual
verification** for each AC (a read-only BigQuery check) in
``test_generator.generate_check`` and this module embeds that generated SQL
directly into the feature file via the generic ``I run the BigQuery check``
steps. ACs for which no valid check could be generated are emitted as
``@manual`` scenarios that report pending (never a false pass).
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
    """
    s = (summary or "").lower()
    if any(k in s for k in ("integration", "ingest", "pipeline", "neo4j", "bigquery", "gcs")):
        return "Integration"
    return "Functional"


def _check_steps(check: dict | None) -> list[str]:
    """Render the executable steps for one AC from its generated check spec.

    A valid ``bq_query`` spec becomes an embedded-SQL BigQuery check; anything
    else (no check generated) becomes a pending manual-verification step so the
    scenario never silently passes.
    """
    check = check or {}
    column = (check.get("assert") or {}).get("column", "violations")
    equals = (check.get("assert") or {}).get("equals", 0)

    if check.get("kind") == "bq_query" and check.get("sql"):
        body = "\n".join("      " + ln for ln in check["sql"].strip().splitlines())
        step = "    When I run the BigQuery check:"
    elif check.get("kind") == "cypher" and check.get("cypher"):
        body = "\n".join("      " + ln for ln in check["cypher"].strip().splitlines())
        step = "    When I run the Neo4j check:"
    else:
        return ["    Then this scenario requires manual verification"]

    return [
        step,
        '      """',
        body,
        '      """',
        f'    Then the result column "{column}" should be {equals}',
    ]


def feature_for_ticket(ticket: str, summary: str, bullets: list[str],
                       test_keys: list[str] | None = None,
                       checks: list[dict | None] | None = None) -> str:
    """Render a complete .feature file from acceptance-criteria bullets.

    ``checks`` is aligned by position with ``bullets`` — each entry is the
    generated check spec (or ``None``). ``test_keys`` likewise carries the Jira
    Test subtask key per scenario so the results sync can target subtasks by tag.
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
    specs = checks or []
    for idx, bullet in enumerate(bullets or [f"acceptance criterion {ticket}-{1}"], start=1):
        check = specs[idx - 1] if idx - 1 < len(specs) else None
        test_key = keys[idx - 1] if idx - 1 < len(keys) else None
        tags = f"{_SCENARIO_TAG} @{ticket}"
        if test_key:
            tags += f" @{test_key}"
        if check is None:
            tags += " @manual"
        feature.append(f"  {tags}")
        feature.append(f"  Scenario: AC{idx} - {bullet[:80].rstrip()}")
        feature.extend(_check_steps(check))
        feature.append("")
    return "\n".join(feature).rstrip() + "\n"
