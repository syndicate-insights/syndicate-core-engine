"""BDD authoring sub-agent.

Reads a Jira ticket's acceptance criteria, generates Cucumber scenarios,
creates Jira `Test` subtasks under the ticket, opens a PR with the new
`.feature` file, and on a failing Harness run inspects the Cucumber report and
opens a follow-up PR to update the affected scenarios.
"""

from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from agent.config import SETTINGS
from agent.observability import agent_callbacks
from agent.sub_agents.bdd_authoring import gherkin
from agent.tools import github_toolset as gh
from agent.tools import harness_toolset as harness
from agent.tools import jira_toolset as jira

BDD_FEATURE_ROOT = "bdd-tests/src/test/resources/feature"


def jira_read_acceptance_criteria(ticket: str) -> dict:
    """Fetch a Jira ticket and return its acceptance-criteria bullets."""
    return jira.acceptance_criteria(ticket)


def author_bdd_scenarios(ticket: str, dry_run: bool = False) -> dict:
    """Generate a feature file for `ticket`, create Jira Test subtasks and open a PR.

    Returns a structured payload describing what was created so the agent can
    explain the result back to the user.
    """
    logger.info("author_bdd_scenarios: start ticket=%s dry_run=%s", ticket, dry_run)
    ac = jira.acceptance_criteria(ticket)
    if "error" in ac:
        logger.error("author_bdd_scenarios: AC fetch failed ticket=%s error=%s", ticket, ac)
        return {"ticket": ticket, "error": ac}
    summary = ac.get("summary") or ticket
    bullets = ac.get("bullets") or []
    logger.info("author_bdd_scenarios: ticket=%s summary=%r bullets=%d", ticket, summary, len(bullets))
    domain = gherkin.domain_for_ticket(ticket, summary)
    logger.info("author_bdd_scenarios: ticket=%s domain=%s", ticket, domain)
    feature_text = gherkin.feature_for_ticket(ticket, summary, bullets)
    feature_path = f"{BDD_FEATURE_ROOT}/{domain}/{gherkin.slugify(ticket)}_{gherkin.slugify(summary, 30)}.feature"
    logger.info("author_bdd_scenarios: ticket=%s feature_path=%s", ticket, feature_path)

    result: dict = {
        "ticket": ticket,
        "summary": summary,
        "domain": domain,
        "feature_path": feature_path,
        "feature": feature_text,
        "test_issues": [],
        "pr": None,
    }
    if dry_run:
        logger.info("author_bdd_scenarios: dry_run=True, skipping Jira + PR creation for ticket=%s", ticket)
        return result

    # 1. Create one Jira Test subtask per Cucumber scenario, tracking each new
    #    key in scenario order so we can tag the corresponding scenario with it.
    logger.info("author_bdd_scenarios: creating %d Jira Test subtask(s) for ticket=%s", len(bullets or ["acceptance criterion 1"]), ticket)
    test_keys: list[str | None] = []
    for idx, bullet in enumerate(bullets or ["acceptance criterion 1"], start=1):
        scenario_summary = f"BDD AC{idx} for {ticket}: {bullet[:120]}"
        issue = jira.create_test_issue(
            ticket=ticket,
            summary=scenario_summary,
            gherkin=feature_text,
            labels=[domain.lower(), "qe-agent"],
        )
        result["test_issues"].append(issue)
        test_keys.append(issue.get("key"))

    logger.info("author_bdd_scenarios: %d Test subtask(s) created for ticket=%s", len(result["test_issues"]), ticket)
    # 2. Re-render the feature with each scenario tagged by its Jira Test subtask
    #    key (@PROJ-123) so the results sync can target subtasks directly.
    feature_text = gherkin.feature_for_ticket(ticket, summary, bullets, test_keys=test_keys)
    result["feature"] = feature_text
    # 3. Branch + write feature file + open PR against the syndicate-core-engine repo.
    logger.info("author_bdd_scenarios: opening PR for ticket=%s feature_path=%s", ticket, feature_path)
    pr_body = _pr_body(ticket, summary, domain, feature_path, ac)
    result["pr"] = gh.author_feature_pr(
        ticket=ticket,
        feature_path=feature_path,
        feature_content=feature_text,
        summary=f"BDD scenarios for {ticket}",
        description=pr_body,
        labels=["qe-agent", "bdd", "auto-generated", domain.lower()],
    )
    logger.info(
        "author_bdd_scenarios: complete ticket=%s pr_url=%s error=%s",
        ticket,
        (result.get("pr") or {}).get("html_url"),
        (result.get("pr") or {}).get("error"),
    )
    return result


def update_bdd_from_failure(ticket: str, plan_execution_id: str | None = None) -> dict:
    """Inspect a failing Harness BDD run and raise a PR with updated Gherkin.

    The agent's LLM step uses this to decide whether to update the scenarios
    (most common when AC has shifted) or leave them and flag a regression.
    """
    if plan_execution_id:
        execution = harness.get_execution(plan_execution_id)
    else:
        execution = harness.latest_bdd_status()
    ac = jira.acceptance_criteria(ticket)
    if "error" in ac:
        return {"ticket": ticket, "harness": execution, "error": ac}
    summary = ac.get("summary") or ticket
    domain = gherkin.domain_for_ticket(ticket, summary)
    feature_text = gherkin.feature_for_ticket(ticket, summary, ac.get("bullets", []))
    feature_path = f"{BDD_FEATURE_ROOT}/{domain}/{gherkin.slugify(ticket)}_{gherkin.slugify(summary, 30)}.feature"
    pr = gh.author_feature_pr(
        ticket=ticket,
        feature_path=feature_path,
        feature_content=feature_text,
        summary=f"Update BDD scenarios for {ticket} after failing Harness run",
        description=_failure_pr_body(ticket, execution, ac),
        labels=["qe-agent", "bdd", "auto-fix"],
    )
    return {"ticket": ticket, "harness": execution, "pr": pr,
            "feature_path": feature_path, "feature": feature_text}


def harness_latest_bdd() -> dict:
    """Latest BDD pipeline execution status (for triage)."""
    return harness.latest_bdd_status()


def jira_sync_results(ticket: str, cucumber_json_path: str | None = None,
                      execution_url: str | None = None,
                      report: list | str | None = None) -> dict:
    """Push Cucumber results to the Jira parent ticket and Test subtasks.

    ``report`` carries the Cucumber JSON content directly (used by the Harness
    CI step); ``cucumber_json_path`` is the local-file fallback for the CLI.
    """
    return jira.sync_cucumber_results(ticket, cucumber_json_path, execution_url, report=report)


def _pr_body(ticket: str, summary: str, domain: str, path: str, ac: dict) -> str:
    bullets = "\n".join(f"- {b}" for b in (ac.get("bullets") or [])) or "- (none extracted)"
    return (
        f"Auto-generated by `qe-quality-agent.bdd_authoring_agent` from {ticket}.\n\n"
        f"**Jira summary:** {summary}\n\n"
        f"**Domain:** {domain}\n\n"
        f"**Feature file:** `{path}`\n\n"
        f"### Acceptance criteria\n{bullets}\n\n"
        "Each `Scenario` has a corresponding Jira `Test` subtask under "
        f"{ticket}. Harness pipeline `bdd_tests` executes these scenarios and "
        "publishes PASS/FAIL comments back to the parent ticket and subtasks.\n"
    )


def _failure_pr_body(ticket: str, execution: dict, ac: dict) -> str:
    bullets = "\n".join(f"- {b}" for b in (ac.get("bullets") or [])) or "- (none extracted)"
    return (
        "The QE agent detected a failing Harness BDD run and re-derived the "
        f"`.feature` file from the latest acceptance criteria of {ticket}.\n\n"
        f"**Harness execution:** {execution.get('ui_url') or execution.get('execution_id')}\n"
        f"**Status:** {execution.get('status')}\n\n"
        f"### Latest acceptance criteria\n{bullets}\n\n"
        "Review the diff carefully — if the AC has not changed, this PR can "
        "be closed and the failure should be triaged as a real regression.\n"
    )


_TOOLS: Iterable[FunctionTool] = [
    FunctionTool(jira_read_acceptance_criteria),
    FunctionTool(author_bdd_scenarios),
    FunctionTool(update_bdd_from_failure),
    FunctionTool(harness_latest_bdd),
    FunctionTool(jira_sync_results),
]


bdd_authoring_agent = LlmAgent(
    name="bdd_authoring_agent",
    model=SETTINGS.model,
    description=(
        "Reads acceptance criteria from a Jira ticket, generates Cucumber BDD "
        "features, creates Jira Test subtasks, opens a GitHub PR against "
        "bdd-tests/, and reconciles failing Harness BDD runs by raising "
        "follow-up PRs to update the Gherkin."
    ),
    instruction=(
        "You are the BDD Authoring sub-agent for the syndicate-core-engine.\n\n"
        "Workflow:\n"
        "1. When given a Jira ticket id, call `jira_read_acceptance_criteria` and "
        "validate the bullets are testable. Refuse politely if the ticket has no AC.\n"
        "2. Call `author_bdd_scenarios(ticket)` to generate the .feature file.\n"
        "   CRITICAL: Use ONLY these step patterns that the Java runner understands:\n"
        "     - Given the QE Quality Agent is reachable\n"
        "     - Given the test suite is \"<suite_name>\"\n"
        "     - When I run scenario \"<scenario_id>\"\n"
        "     - Then the scenario status should be PASS\n"
        "     - Then there should be no findings\n"
        "   Map the Jira AC bullet to the most relevant deterministic scenario ID "
        "   from the available suites (static_analysis, coding_standards, integration, "
        "   functional, non_functional).\n"
        "3. Include the PR url and Test subtask keys in your reply.\n"
        "4. When asked about a failing Harness BDD run, call `harness_latest_bdd` "
        "(or `update_bdd_from_failure` when given an execution id) and explain "
        "whether the failure looks like a Gherkin update or a real regression.\n"
        "5. Never modify pipeline source code; you only write `.feature` files "
        "under `bdd-tests/src/test/resources/feature/`.\n"
        "6. Always cite the Jira ticket, the Harness pipeline, and the PR url so "
        "the human can audit.\n"
    ),
    tools=list(_TOOLS),
    **agent_callbacks(),
)
