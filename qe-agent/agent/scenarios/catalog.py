"""Catalog of the non-BDD deterministic checks.

CodingStandards (CS), StaticAnalysis (SA) and NonFunctional (N) are the same on
every ticket — they validate the repo/platform, not a ticket's acceptance
criteria. The BDD authoring agent uses this catalog to create one fixed Jira
``Test`` subtask per check when a ticket is created (no ``.feature`` files are
written for these — Harness runs them via the agent's ``/qe/scenario`` API), and
the run-sync endpoint uses it to know which checks exist.

Each entry: (suite, scenario_id, title, acceptance_criteria).
"""

from __future__ import annotations

# suite key (as registered in scenarios.runner.SUITES) -> list of checks
NON_BDD_CHECKS: dict[str, list[tuple[str, str, str]]] = {
    "standards": [
        ("CS1", "dbt model naming convention",
         "All dbt models follow the stg_/_enriched naming convention."),
        ("CS2", "Primary-key data tests declared",
         "Every enriched model declares a not_null + unique test on its primary key."),
        ("CS3", "dbt sources documented",
         "All dbt sources have descriptions and column documentation."),
        ("CS4", "Kubernetes manifest hygiene",
         "K8s manifests pin image tags, set resource limits and a serviceAccountName."),
        ("CS5", "Foreign-key naming consistency",
         "Foreign-key columns follow the <entity>_id naming convention across models."),
    ],
    "static": [
        ("SA1", "dbt SQL lint (sqlfluff)",
         "dbt SQL passes sqlfluff with no lint violations."),
        ("SA2", "Python lint (ruff)",
         "Python sources pass ruff with no lint errors."),
        ("SA3", "Python security scan (bandit)",
         "Python sources have no HIGH/MEDIUM bandit security findings."),
        ("SA4", "YAML lint (yamllint)",
         "YAML files pass yamllint with no errors."),
        ("SA5", "Hardcoded secret / credential scan",
         "No hardcoded secrets or credentials are present in the repository."),
    ],
    "nonfunctional": [
        ("N1", "Performance / SLA",
         "The transform pipeline completes within the documented latency SLA."),
        ("N2", "Reliability / Security posture",
         "Recent runs show no fatal errors and the security posture checks pass."),
    ],
}


def all_checks() -> list[tuple[str, str, str, str]]:
    """Flatten the catalog to (suite, scenario_id, title, acceptance_criteria)."""
    return [
        (suite, sid, title, ac)
        for suite, checks in NON_BDD_CHECKS.items()
        for (sid, title, ac) in checks
    ]
