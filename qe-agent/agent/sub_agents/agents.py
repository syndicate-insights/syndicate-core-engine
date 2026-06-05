"""The five QE sub-agents."""

from __future__ import annotations

from agent.sub_agents.base import make_sub_agent

static_analysis_agent = make_sub_agent(
    name="static_analysis_agent",
    suite_key="static",
    description="Static code analysis: sqlfluff, ruff, bandit, yamllint, secret scanning.",
    focus="Find code-level defects, style violations and hardcoded secrets before runtime.",
)

coding_standards_agent = make_sub_agent(
    name="coding_standards_agent",
    suite_key="standards",
    description="Repo-specific coding-standard governance for dbt, Kubernetes and FK naming.",
    focus="Enforce dbt naming, mandatory PK tests, documented sources, and k8s manifest hygiene.",
)

integration_test_agent = make_sub_agent(
    name="integration_test_agent",
    suite_key="integration",
    description="Integration tests across GCS -> BigQuery -> Neo4j (5 scenarios).",
    focus="Verify data flows and parity between every stage of the pipeline.",
)

functional_test_agent = make_sub_agent(
    name="functional_test_agent",
    suite_key="functional",
    description="Functional tests of business transformation rules (6 scenarios).",
    focus="Validate INVESTMENT reclassification, address composition, phone normalisation, "
    "dbt data tests and Neo4j constraints.",
)

non_functional_test_agent = make_sub_agent(
    name="non_functional_test_agent",
    suite_key="nonfunctional",
    description="Non-functional tests: performance/SLA and reliability/security (2 scenarios).",
    focus="Check job durations against SLAs, BigQuery latency, resource limits, safe cronjob "
    "policies and error-free logs.",
)

ALL_SUB_AGENTS = [
    static_analysis_agent,
    coding_standards_agent,
    integration_test_agent,
    functional_test_agent,
    non_functional_test_agent,
]
