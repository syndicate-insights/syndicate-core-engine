"""Centralised configuration for the QE Quality Agent.

All values are sourced from environment variables so the same image runs
locally, in GKE, and inside Harness CI. Defaults mirror the discovered
`syndicate-core-engine` infrastructure so the agent works out of the box.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the QE agent and all toolsets."""

    # --- GCP ---
    gcp_project: str = field(default_factory=lambda: _env("GCP_PROJECT", "project-61358164-b71e-4422-a5c"))
    bq_dataset: str = field(default_factory=lambda: _env("BQ_DATASET", "qe_hack_syndicate_insight"))
    bq_location: str = field(default_factory=lambda: _env("BQ_LOCATION", "us-central1"))
    gcs_bucket: str = field(default_factory=lambda: _env("GCS_BUCKET_NAME", "qe_hack_syndicate_raw"))

    # Optional GCP SA impersonation. Empty by default: the agent uses ambient
    # Workload Identity (the same `qe-hack-syndicate-k8s-sa` KSA the dbt jobs and
    # the synthetic data generator use), so no impersonation hop is required.
    impersonate_sa: str = field(
        default_factory=lambda: _env("IMPERSONATE_SERVICE_ACCOUNT", "")
    )

    # --- Vertex AI / Gemini ---
    use_vertex: bool = field(default_factory=lambda: _env("GOOGLE_GENAI_USE_VERTEXAI", "1") == "1")
    vertex_location: str = field(default_factory=lambda: _env("GOOGLE_CLOUD_LOCATION", "us-central1"))
    model: str = field(default_factory=lambda: _env("QE_AGENT_MODEL", "gemini-2.0-flash"))

    # --- Neo4j ---
    neo4j_uri: str = field(default_factory=lambda: _env("NEO4J_URI", "bolt://neo4j.qe-hack-syndicate.svc.cluster.local:7687"))
    neo4j_user: str = field(default_factory=lambda: _env("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _env("NEO4J_PASSWORD", ""))

    # --- Kubernetes ---
    namespace: str = field(default_factory=lambda: _env("K8S_NAMESPACE", "qe-hack-syndicate"))

    # --- Repo / dbt ---
    repo_root: str = field(default_factory=lambda: _env("REPO_ROOT", "/workspace/syndicate-core-engine"))

    # --- Non-functional SLA thresholds ---
    dbt_job_sla_seconds: int = field(default_factory=lambda: int(_env("DBT_JOB_SLA_SECONDS", "3600")))
    ingest_sla_seconds: int = field(default_factory=lambda: int(_env("INGEST_SLA_SECONDS", "1800")))
    bq_query_sla_seconds: float = field(default_factory=lambda: float(_env("BQ_QUERY_SLA_SECONDS", "10")))

    @property
    def dbt_projects(self) -> dict[str, str]:
        """Map of entity -> dbt project directory (relative to repo_root)."""
        return {
            "account": "dbt-account-transform",
            "address": "dbt-address-transform",
            "customer": "dbt-customer-transform",
        }

    @property
    def enriched_tables(self) -> dict[str, str]:
        return {
            "account": "account_enriched",
            "address": "address_enriched",
            "customer": "customer_enriched",
        }

    @property
    def raw_tables(self) -> dict[str, str]:
        return {
            "account": "account_raw_data",
            "address": "address_raw_data",
            "customer": "customer_raw_data",
        }

    def fq_table(self, table: str) -> str:
        """Fully-qualified BigQuery table id."""
        return f"{self.gcp_project}.{self.bq_dataset}.{table}"


SETTINGS = Settings()
