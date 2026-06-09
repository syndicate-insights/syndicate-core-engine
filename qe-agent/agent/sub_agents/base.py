"""Factory helpers shared by every QE sub-agent.

The deterministic scenario functions are wrapped as ADK FunctionTools so the LLM
can run a specific scenario or a whole suite and then reason about / triage the
JSON results. Pass/fail gating stays deterministic (the JSON `status` field);
the model only explains, correlates and summarises.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from agent.config import SETTINGS
from agent.observability import agent_callbacks
from agent.scenarios import runner


def run_scenario(suite: str, scenario_id: str) -> dict:
    """Run a single QE scenario. Returns a JSON result with status PASS/FAIL/ERROR."""
    return runner.run_scenario(suite, scenario_id)


def run_suite(suite: str) -> dict:
    """Run every scenario in a suite and return aggregated pass/fail JSON."""
    return runner.run_suite(suite)


SCENARIO_TOOLS = [FunctionTool(run_scenario), FunctionTool(run_suite)]


def make_sub_agent(name: str, suite_key: str, description: str, focus: str) -> LlmAgent:
    """Build a standard QE sub-agent bound to one suite plus extra tools."""
    return LlmAgent(
        name=name,
        model=SETTINGS.model,
        description=description,
        instruction=(
            f"You are the {name} for the syndicate-core-engine data pipeline.\n"
            f"Your suite key is '{suite_key}'. {focus}\n\n"
            "Workflow:\n"
            "1. Call run_suite (or run_scenario for a specific id) to execute the "
            "deterministic checks.\n"
            "2. NEVER override the deterministic `status`. Treat FAIL/ERROR as authoritative.\n"
            "3. For any FAIL/ERROR, write a concise root-cause hypothesis and a concrete "
            "remediation step referencing the affected model/table/manifest.\n"
            "4. Return a short triage summary plus the raw JSON results.\n"
            "You are read-only apart from running dbt tests; never attempt to mutate data."
        ),
        tools=SCENARIO_TOOLS,
        **agent_callbacks(),
    )
