"""Kubernetes toolset: inspect pods/jobs/cronjobs + logs in the namespace.

Permissions (least-privilege RBAC):
  - get/list pods, pods/log, jobs, cronjobs
  - create jobs (only to trigger a `dbt test` run)
The agent loads in-cluster config when running on GKE, else local kubeconfig.
"""

from __future__ import annotations

from functools import lru_cache

from kubernetes import client, config

from agent.config import SETTINGS


@lru_cache(maxsize=1)
def _load() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _core() -> "client.CoreV1Api":
    _load()
    return client.CoreV1Api()


def _batch() -> "client.BatchV1Api":
    _load()
    return client.BatchV1Api()


def list_pods(label_selector: str = "") -> dict:
    pods = _core().list_namespaced_pod(SETTINGS.namespace, label_selector=label_selector or None)
    return {
        "namespace": SETTINGS.namespace,
        "pods": [
            {
                "name": p.metadata.name,
                "phase": p.status.phase,
                "start_time": p.status.start_time.isoformat() if p.status.start_time else None,
                "service_account": p.spec.service_account_name,
            }
            for p in pods.items
        ],
    }


def pod_logs(pod_name: str, tail_lines: int = 200, container: str | None = None) -> dict:
    try:
        logs = _core().read_namespaced_pod_log(
            name=pod_name,
            namespace=SETTINGS.namespace,
            tail_lines=tail_lines,
            container=container,
        )
    except client.ApiException as exc:  # noqa: PERF203
        return {"pod": pod_name, "error": str(exc)}
    return {"pod": pod_name, "log": logs}


def list_cronjobs() -> dict:
    cronjobs = _batch().list_namespaced_cron_job(SETTINGS.namespace)
    return {
        "cronjobs": [
            {
                "name": c.metadata.name,
                "schedule": c.spec.schedule,
                "concurrency_policy": c.spec.concurrency_policy,
                "last_schedule": c.status.last_schedule_time.isoformat()
                if c.status.last_schedule_time
                else None,
                "suspend": c.spec.suspend,
            }
            for c in cronjobs.items
        ]
    }


def list_jobs(label_selector: str = "") -> dict:
    jobs = _batch().list_namespaced_job(SETTINGS.namespace, label_selector=label_selector or None)
    out = []
    for j in jobs.items:
        completion = None
        duration = None
        if j.status.start_time and j.status.completion_time:
            duration = (j.status.completion_time - j.status.start_time).total_seconds()
        out.append(
            {
                "name": j.metadata.name,
                "succeeded": j.status.succeeded or 0,
                "failed": j.status.failed or 0,
                "start_time": j.status.start_time.isoformat() if j.status.start_time else None,
                "completion_time": j.status.completion_time.isoformat()
                if j.status.completion_time
                else None,
                "duration_seconds": duration,
            }
        )
        _ = completion
    return {"jobs": out}


def latest_job_for(name_prefix: str) -> dict:
    """Most recent Job whose name starts with `name_prefix` (e.g. a cronjob name)."""
    jobs = _batch().list_namespaced_job(SETTINGS.namespace).items
    matching = [j for j in jobs if j.metadata.name.startswith(name_prefix)]
    if not matching:
        return {"name_prefix": name_prefix, "found": False}
    latest = max(matching, key=lambda j: j.metadata.creation_timestamp)
    duration = None
    if latest.status.start_time and latest.status.completion_time:
        duration = (latest.status.completion_time - latest.status.start_time).total_seconds()
    return {
        "name_prefix": name_prefix,
        "found": True,
        "name": latest.metadata.name,
        "succeeded": latest.status.succeeded or 0,
        "failed": latest.status.failed or 0,
        "duration_seconds": duration,
    }
