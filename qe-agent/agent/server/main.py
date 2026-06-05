"""FastAPI server for the QE Quality Agent.

Two surfaces share one process:
  - ADK agent surface (`/run`, `/run_sse`, session APIs) via `get_fast_api_app`,
    used for LLM-driven triage / interactive quality sweeps.
  - Deterministic QE surface (`/qe/...`) that Harness CI calls to run a suite or
    a single scenario and gate purely on the JSON `status` field.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from agent.scenarios import runner

AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_adk_app() -> FastAPI | None:
    """Best-effort construction of the ADK FastAPI app.

    Kept optional so the deterministic QE endpoints still serve even if the ADK
    web assets / model backend are unavailable (e.g. in a minimal CI image).
    """
    try:
        from google.adk.cli.fast_api import get_fast_api_app

        return get_fast_api_app(agents_dir=AGENTS_DIR, web=True)
    except Exception:  # noqa: BLE001
        return None


app: FastAPI = _build_adk_app() or FastAPI(title="QE Quality Agent")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/qe/scenarios")
def list_scenarios() -> dict:
    """List all available suites and their scenario ids."""
    return runner.list_scenarios()


@app.get("/qe/suite/{suite}")
def run_suite(suite: str) -> dict:
    """Run every scenario in a suite. `passed` is the deterministic gate."""
    return runner.run_suite(suite)


@app.get("/qe/scenario/{suite}/{scenario_id}")
def run_scenario(suite: str, scenario_id: str) -> dict:
    """Run a single scenario by id."""
    return runner.run_scenario(suite, scenario_id)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))  # noqa: S104


if __name__ == "__main__":
    main()
