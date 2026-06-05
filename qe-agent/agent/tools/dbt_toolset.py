"""dbt toolset: parse/compile/test the three transform projects (read + test).

`dbt test` is the only execution the agent performs locally; it does not run
`dbt run` (no data mutation). Output is parsed from dbt's run_results.json.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from agent.config import SETTINGS


def _project_dir(entity: str) -> Path:
    return Path(SETTINGS.repo_root) / SETTINGS.dbt_projects[entity]


def _env() -> dict:
    env = dict(os.environ)
    env.setdefault("GCP_PROJECT", SETTINGS.gcp_project)
    env.setdefault("BQ_DATASET", SETTINGS.bq_dataset)
    return env


def _run_dbt(entity: str, args: list[str]) -> dict:
    proj = _project_dir(entity)
    if not proj.exists():
        return {"entity": entity, "error": f"project dir not found: {proj}"}
    cmd = ["dbt", *args, "--project-dir", str(proj), "--profiles-dir", str(proj)]
    completed = subprocess.run(  # noqa: S603
        cmd, cwd=str(proj), env=_env(), capture_output=True, text=True, timeout=900
    )
    return {
        "entity": entity,
        "command": " ".join(args),
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def dbt_parse(entity: str) -> dict:
    """Validate that a dbt project parses (catches Jinja/ref errors)."""
    return _run_dbt(entity, ["parse"])


def dbt_compile(entity: str) -> dict:
    return _run_dbt(entity, ["compile"])


def dbt_test(entity: str, select: str = "") -> dict:
    """Run dbt schema/data tests and parse run_results.json."""
    args = ["test"]
    if select:
        args += ["--select", select]
    result = _run_dbt(entity, args)
    run_results = _project_dir(entity) / "target" / "run_results.json"
    if run_results.exists():
        data = json.loads(run_results.read_text())
        summary = {"pass": 0, "fail": 0, "error": 0, "skip": 0, "tests": []}
        for r in data.get("results", []):
            status = r.get("status", "")
            summary[status] = summary.get(status, 0) + 1
            summary["tests"].append(
                {"name": r.get("unique_id"), "status": status, "failures": r.get("failures")}
            )
        result["test_summary"] = summary
    return result
