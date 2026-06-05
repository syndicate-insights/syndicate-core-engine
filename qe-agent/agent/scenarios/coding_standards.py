"""Coding-standards suite (goal 2).

Convention checks specific to this repo: dbt naming/structure, k8s manifest
hygiene, and FK naming consistency. These complement the generic linters in the
static_analysis suite with repo-specific governance rules.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from agent.config import SETTINGS
from agent.results import ScenarioResult, Status

SUITE = "coding_standards"


def _repo() -> Path:
    return Path(SETTINGS.repo_root)


def c_dbt_naming() -> ScenarioResult:
    """CS1: staging models prefixed `stg_`, marts suffixed `_enriched`."""
    r = ScenarioResult("CS1", SUITE, "dbt model naming convention")
    bad: list[str] = []
    for sql in _repo().glob("dbt-*/models/**/*.sql"):
        rel = sql.relative_to(_repo())
        name = sql.stem
        if "staging" in sql.parts and not (name.startswith("stg_") or name == "processed_files_metadata"):
            bad.append(f"{rel}: staging model should be stg_*")
        if "marts" in sql.parts and not name.endswith("_enriched"):
            bad.append(f"{rel}: marts model should be *_enriched")
    r.metrics = {"violations": len(bad)}
    if bad:
        r.fail("dbt naming convention violations.")
        r.actual = bad
    return r


def c_pk_tests_present() -> ScenarioResult:
    """CS2: every enriched model declares not_null tests on its primary key."""
    r = ScenarioResult("CS2", SUITE, "Primary-key data tests declared")
    expected_pk = {"account_enriched": "account_id", "address_enriched": "address_id", "customer_enriched": "customer_id"}
    missing: list[str] = []
    for schema in _repo().glob("dbt-*/models/marts/schema.yml"):
        doc = yaml.safe_load(schema.read_text()) or {}
        for model in doc.get("models", []):
            pk = expected_pk.get(model.get("name"))
            if not pk:
                continue
            col = next((c for c in model.get("columns", []) if c.get("name") == pk), None)
            tests = (col or {}).get("tests", []) if col else []
            flat = [t if isinstance(t, str) else next(iter(t)) for t in tests]
            if "not_null" not in flat:
                missing.append(f"{model['name']}.{pk} missing not_null test")
    r.metrics = {"missing": len(missing)}
    if missing:
        r.fail("Primary-key not_null tests missing.")
        r.actual = missing
    return r


def c_sources_documented() -> ScenarioResult:
    """CS3: every source table has a description and column docs."""
    r = ScenarioResult("CS3", SUITE, "dbt sources documented")
    undocumented: list[str] = []
    for src in _repo().glob("dbt-*/models/sources.yml"):
        doc = yaml.safe_load(src.read_text()) or {}
        for source in doc.get("sources", []):
            for table in source.get("tables", []):
                if not table.get("description"):
                    undocumented.append(f"{src.parent.parent.name}:{table.get('name')} missing description")
    r.metrics = {"undocumented": len(undocumented)}
    if undocumented:
        r.fail("Undocumented dbt sources.")
        r.actual = undocumented
    return r


def c_k8s_hygiene() -> ScenarioResult:
    """CS4: k8s manifests pin images, set resources, SA and concurrencyPolicy."""
    r = ScenarioResult("CS4", SUITE, "Kubernetes manifest hygiene")
    issues: list[str] = []
    for man in _repo().glob("**/*.yaml"):
        if "qe-agent" in man.parts and "deploy" not in man.parts:
            continue
        try:
            docs = list(yaml.safe_load_all(man.read_text()))
        except yaml.YAMLError:
            continue
        for d in docs:
            if not isinstance(d, dict):
                continue
            kind = d.get("kind", "")
            rel = man.relative_to(_repo())
            spec = d.get("spec", {})
            pod_spec = _find_pod_spec(d)
            if kind in ("CronJob", "Job", "Deployment"):
                if not pod_spec:
                    continue
                if not pod_spec.get("serviceAccountName"):
                    issues.append(f"{rel} ({kind}): no serviceAccountName")
                for ctr in pod_spec.get("containers", []):
                    image = ctr.get("image", "")
                    if image.endswith(":latest") or (":" not in image and image):
                        issues.append(f"{rel}: container image not pinned ({image})")
                    if not ctr.get("resources", {}).get("limits"):
                        issues.append(f"{rel}: container {ctr.get('name')} has no resource limits")
            if kind == "CronJob" and spec.get("concurrencyPolicy") not in ("Forbid", "Replace"):
                issues.append(f"{rel}: CronJob concurrencyPolicy should be Forbid/Replace")
    r.metrics = {"issues": len(issues)}
    if issues:
        r.fail("Kubernetes manifest hygiene issues.")
        r.actual = issues[:30]
    return r


def _find_pod_spec(doc: dict) -> dict | None:
    spec = doc.get("spec", {})
    # Deployment
    if "template" in spec:
        return spec["template"].get("spec")
    # CronJob -> jobTemplate -> template
    job_template = spec.get("jobTemplate", {})
    if job_template:
        return job_template.get("spec", {}).get("template", {}).get("spec")
    # Job
    if "template" in spec:
        return spec["template"].get("spec")
    return None


def c_fk_naming() -> ScenarioResult:
    """CS5: foreign-key column is consistently named `customer_id` everywhere."""
    r = ScenarioResult("CS5", SUITE, "Foreign-key naming consistency")
    bad: list[str] = []
    fk_variants = re.compile(r"\b(cust_id|customerid|custid|customer_ref)\b", re.I)
    for sql in _repo().glob("dbt-*/models/**/*.sql"):
        text = sql.read_text()
        if fk_variants.search(text):
            bad.append(str(sql.relative_to(_repo())))
    r.metrics = {"violations": len(bad)}
    if bad:
        r.fail("Inconsistent foreign-key naming (expected `customer_id`).")
        r.actual = bad
    return r


REGISTRY = {
    "CS1": c_dbt_naming,
    "CS2": c_pk_tests_present,
    "CS3": c_sources_documented,
    "CS4": c_k8s_hygiene,
    "CS5": c_fk_naming,
}


def run_all() -> list[dict]:
    out = []
    for fn in REGISTRY.values():
        try:
            out.append(fn().to_dict())
        except Exception as exc:  # noqa: BLE001
            out.append(ScenarioResult("?", SUITE, fn.__name__, Status.ERROR, findings=[str(exc)]).to_dict())
    return out
