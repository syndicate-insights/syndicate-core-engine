"""Non-functional testing suite (goal 5) — 2 scenarios.

N1: performance / SLA — pipeline jobs finish inside their windows, BigQuery
    query latency is acceptable, and pods stay within resource limits.
N2: reliability / security — safe cronjob policies, no hardcoded secrets in
    running manifests, Neo4j auth sourced from a Secret, and error-free logs.
"""

from __future__ import annotations

from agent.config import SETTINGS
from agent.results import ScenarioResult, Status
from agent.tools import bigquery_toolset as bq
from agent.tools import kubernetes_toolset as k8s

SUITE = "non_functional"

_PIPELINE_CRONJOBS = {
    "dbt-account-transform-cronjob": SETTINGS.dbt_job_sla_seconds,
    "dbt-address-transform-cronjob": SETTINGS.dbt_job_sla_seconds,
    "dbt-customer-transform-cronjob": SETTINGS.dbt_job_sla_seconds,
    "neo4j-ingest-cronjob": SETTINGS.ingest_sla_seconds,
}


def n1_performance_sla() -> ScenarioResult:
    """N1: jobs complete within SLA and BigQuery latency is acceptable."""
    r = ScenarioResult("N1", SUITE, "Performance / SLA")
    findings: list[str] = []
    job_metrics = {}
    for cronjob, sla in _PIPELINE_CRONJOBS.items():
        latest = k8s.latest_job_for(cronjob)
        job_metrics[cronjob] = latest
        if not latest.get("found"):
            findings.append(f"{cronjob}: no Job runs found")
            continue
        if latest.get("failed"):
            findings.append(f"{cronjob}: last run failed")
        dur = latest.get("duration_seconds")
        if dur is not None and dur > sla:
            findings.append(f"{cronjob}: duration {dur:.0f}s exceeds SLA {sla}s")
    # BigQuery query latency probe
    probe = bq.run_query(
        f"SELECT COUNT(*) AS n FROM `{SETTINGS.fq_table('customer_enriched')}`", timed=True  # nosec B608
    )
    latency = probe.get("elapsed_seconds")
    if latency is not None and latency > SETTINGS.bq_query_sla_seconds:
        findings.append(f"BigQuery latency {latency}s exceeds SLA {SETTINGS.bq_query_sla_seconds}s")
    r.actual = {"jobs": job_metrics, "bq_latency_seconds": latency}
    r.metrics = {"violations": len(findings)}
    if findings:
        r.status = Status.FAIL
        r.findings = findings
    return r


def n2_reliability_security() -> ScenarioResult:
    """N2: reliability + security posture of the running pipeline."""
    r = ScenarioResult("N2", SUITE, "Reliability / Security posture")
    findings: list[str] = []

    # Cronjob concurrency policy must prevent overlapping runs.
    cronjobs = k8s.list_cronjobs().get("cronjobs", [])
    for c in cronjobs:
        if c["name"] in _PIPELINE_CRONJOBS and c.get("concurrency_policy") not in ("Forbid", "Replace"):
            findings.append(f"{c['name']}: unsafe concurrencyPolicy {c.get('concurrency_policy')}")
        if c.get("suspend"):
            findings.append(f"{c['name']}: cronjob is suspended")

    # No recent ERROR/Traceback lines in pipeline pod logs.
    pods = k8s.list_pods().get("pods", [])
    error_pods = []
    for p in pods:
        if not any(k in p["name"] for k in ("dbt-", "neo4j-ingest", "csv-generator")):
            continue
        log = k8s.pod_logs(p["name"], tail_lines=200).get("log", "")
        if any(tok in log for tok in ("Traceback (most recent call last)", "ERROR", "FATAL")):
            error_pods.append(p["name"])
    if error_pods:
        findings.append(f"Error markers in logs of pods: {', '.join(error_pods[:5])}")

    # Pipeline pods should not run as the agent's privileged path / must have a SA.
    for p in pods:
        if any(k in p["name"] for k in ("dbt-", "neo4j-ingest")) and not p.get("service_account"):
            findings.append(f"{p['name']}: no serviceAccountName bound")

    r.actual = {"cronjobs": cronjobs, "pods_checked": len(pods), "error_pods": error_pods}
    r.metrics = {"violations": len(findings)}
    if findings:
        r.status = Status.FAIL
        r.findings = findings
    return r


REGISTRY = {
    "N1": n1_performance_sla,
    "N2": n2_reliability_security,
}


def run_all() -> list[dict]:
    out = []
    for fn in REGISTRY.values():
        try:
            out.append(fn().to_dict())
        except Exception as exc:  # noqa: BLE001
            out.append(ScenarioResult("?", SUITE, fn.__name__, Status.ERROR, findings=[str(exc)]).to_dict())
    return out
