"""Static code analysis suite (goal 1).

Runs language-appropriate analysers across the repo and converts their output
into deterministic ScenarioResults. The LLM layer summarises/triages findings;
gating is based on tool exit codes / finding counts.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from agent.config import SETTINGS
from agent.results import ScenarioResult, Status

SUITE = "static_analysis"


def _repo() -> Path:
    return Path(SETTINGS.repo_root)


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        cmd, cwd=str(cwd or _repo()), capture_output=True, text=True, timeout=600
    )


def s_sql_lint() -> ScenarioResult:
    """SA1: sqlfluff lint of all dbt models."""
    r = ScenarioResult("SA1", SUITE, "dbt SQL lint (sqlfluff)")
    # Use rglob to ensure we find all .sql files inside any dbt project folder
    models = [
        str(p) for p in _repo().rglob("*.sql") 
        if "dbt-" in str(p) and "/models/" in str(p)
    ]
    if not models:
        return r.error(f"No dbt model files found under repo_root ({_repo()}).")
    proc = _run(["sqlfluff", "lint", "--format", "json", "--dialect", "bigquery", *models])
    try:
        violations = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return r.error(f"sqlfluff produced no parseable output: {proc.stderr[:300]}")
    total = sum(len(f.get("violations", [])) for f in violations)
    r.metrics = {"files_checked": len(models), "violations": total}
    if total:
        r.fail(f"sqlfluff reported {total} style violations across dbt models.")
        # Report the exact line + rule for each violation so it's fixable
        # straight from the JSON output.
        # sqlfluff renamed these keys across versions (line_no -> start_line_no),
        # so read whichever the installed version emits.
        r.actual = [
            {"file": f["filepath"],
             "line": v.get("start_line_no") or v.get("line_no"),
             "col": v.get("start_line_pos") or v.get("line_pos"),
             "code": v.get("code"), "description": v.get("description")}
            for f in violations
            for v in f.get("violations", [])
        ][:30]
    return r


def s_python_lint() -> ScenarioResult:
    """SA2: ruff lint of all Python (agent + any embedded scripts)."""
    r = ScenarioResult("SA2", SUITE, "Python lint (ruff)")
    proc = _run(["ruff", "check", "--output-format", "json", str(_repo())])
    try:
        issues = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        issues = []
    r.metrics = {"issues": len(issues)}
    if issues:
        r.fail(f"ruff reported {len(issues)} lint issues.")
        r.actual = [
            {"file": i["filename"], "line": (i.get("location") or {}).get("row"),
             "code": i["code"], "msg": i["message"]}
            for i in issues[:30]
        ]
    return r


def s_security_scan() -> ScenarioResult:
    """SA3: bandit security scan of Python code."""
    r = ScenarioResult("SA3", SUITE, "Python security scan (bandit)")
    proc = _run(["bandit", "-r", str(_repo() / "qe-agent"), "-f", "json", "-q"])
    try:
        report = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return r.error("bandit produced no parseable output.")
    results = report.get("results", [])
    high = [x for x in results if x.get("issue_severity") in ("HIGH", "MEDIUM")]
    r.metrics = {"total_findings": len(results), "high_or_medium": len(high)}
    if high:
        r.fail(f"bandit found {len(high)} HIGH/MEDIUM severity issues.")
        r.actual = [
            {"file": x["filename"], "line": x.get("line_number"),
             "test": x["test_name"], "sev": x["issue_severity"]}
            for x in high[:30]
        ]
    return r


def s_yaml_lint() -> ScenarioResult:
    """SA4: yamllint of all k8s / pipeline YAML."""
    r = ScenarioResult("SA4", SUITE, "YAML lint (yamllint)")
    yamls = [str(p) for p in _repo().glob("**/*.yaml") if "target" not in str(p)]
    if not yamls:
        return r.error("No YAML files found.")
    proc = _run(["yamllint", "-f", "parsable", "-d", "{extends: relaxed, rules: {line-length: disable}}", *yamls])
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    errors = [ln for ln in lines if "[error]" in ln]
    r.metrics = {"files_checked": len(yamls), "warnings": len(lines) - len(errors), "errors": len(errors)}
    if errors:
        r.fail(f"yamllint reported {len(errors)} errors.")
        r.actual = errors[:20]
    return r


def s_secret_scan() -> ScenarioResult:
    """SA5: heuristic scan for hardcoded secrets / credentials in YAML & SQL."""
    r = ScenarioResult("SA5", SUITE, "Hardcoded secret / credential scan")
    patterns = {
        "neo4j_password": re.compile(r"changeme|NEO4J_AUTH\s*=\s*\w+/\w+", re.I),
        "git_token_literal": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        "private_key": re.compile(r"BEGIN (RSA |EC )?PRIVATE KEY"),
        "password_literal": re.compile(r"password\s*[:=]\s*[\"']?[^\s\"'{}$]{6,}", re.I),
    }
    hits: list[dict] = []
    for path in _repo().glob("**/*"):
        if path.suffix.lower() not in (".yaml", ".yml", ".sql", ".env") or not path.is_file():
            continue
        if "qe-agent/agent/tools/credentials" in str(path):
            continue
        text = path.read_text(errors="ignore")
        for name, pat in patterns.items():
            for m in pat.finditer(text):
                # ignore obvious env-var templating / placeholders
                if "env_var" in m.group(0) or "valueFrom" in text[max(0, m.start() - 60):m.start()]:
                    continue
                line = text.count("\n", 0, m.start()) + 1
                hits.append({"file": str(path.relative_to(_repo())), "type": name, "line": line})
    r.metrics = {"hits": len(hits)}
    if hits:
        r.fail(f"Found {len(hits)} potential hardcoded secrets.")
        r.actual = hits[:20]
    return r


REGISTRY = {
    "SA1": s_sql_lint,
    "SA2": s_python_lint,
    "SA3": s_security_scan,
    "SA4": s_yaml_lint,
    "SA5": s_secret_scan,
}


def run_all() -> list[dict]:
    out = []
    for fn in REGISTRY.values():
        try:
            out.append(fn().to_dict())
        except Exception as exc:  # noqa: BLE001
            out.append(ScenarioResult("?", SUITE, fn.__name__, Status.ERROR, findings=[str(exc)]).to_dict())
    return out
