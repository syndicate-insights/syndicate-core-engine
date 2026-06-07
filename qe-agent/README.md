# QE Quality Agent

A Quality Engineering AI agent built with the **Google Agent Development Kit
(ADK)** and **Gemini on Vertex AI**. It tests the `syndicate-core-engine` data
pipeline end to end:

```
synthetic-data-generator ─▶ GCS (qe_hack_syndicate_raw)
        ─▶ dbt transforms ─▶ BigQuery (raw ─▶ enriched)
        ─▶ neo4j-ingest ─▶ Neo4j graph        (all on GKE, ns: qe-hack-syndicate)
```

The agent delivers the five QE goals:

| # | Goal | Suite key | Scenarios |
|---|------|-----------|-----------|
| 1 | Static code analysis        | `static`        | SA1–SA5 (5) |
| 2 | Coding standard checks      | `standards`     | CS1–CS5 (5) |
| 3 | Integration testing         | `integration`   | I1–I5 (5)   |
| 4 | Functional testing          | `functional`    | F1–F6 (6)   |
| 5 | Non-functional testing      | `nonfunctional` | N1–N2 (2)   |

A sixth goal — **BDD authoring** — is delivered by the
[`bdd_authoring_agent`](agent/sub_agents/bdd_authoring/agent.py) sub-agent: it
reads acceptance criteria from a Jira ticket, generates Cucumber feature files
in [`bdd-tests/`](../bdd-tests/README.md), creates Jira `Test` issues linked to
the originating story, opens a GitHub PR, and reconciles failing Harness BDD
runs by raising follow-up PRs against the Gherkin.

See [`docs/integrations.md`](docs/integrations.md) for the full
GitHub <-> Jira <-> Agent <-> Harness wiring on GKE.

Checks are **deterministic** (each returns a JSON `status` of `PASS`/`FAIL`/
`ERROR`); Gemini is used for static analysis reasoning, standards review and
failure triage/summaries. Harness gates strictly on the deterministic status.

---

## Architecture

```
qe_orchestrator (LlmAgent, Gemini)
├── static_analysis_agent      ── static suite
├── coding_standards_agent     ── standards suite
├── integration_test_agent     ── integration suite
├── functional_test_agent      ── functional suite
├── non_functional_test_agent  ── nonfunctional suite
└── bdd_authoring_agent        ── Jira AC -> Cucumber features -> Jira Test issues -> GitHub PRs

Toolsets (FunctionTools, impersonated GCP SA + WI):
  gcs_toolset · bigquery_toolset · neo4j_toolset · kubernetes_toolset · dbt_toolset
  jira_toolset · harness_toolset · github_toolset
```

Surfaces (one process, port `8080`):

- **ADK agent API** (`/run`, `/run_sse`, sessions) — LLM-driven sweeps & triage.
- **Deterministic QE API** — what Harness calls:
  - `GET /qe/scenarios` — list suites + scenario ids
  - `GET /qe/suite
- **BDD authoring API** — used by the BDD pipeline and by humans via `qe-cli`:
  - `GET  /qe/jira/{ticket}/acceptance-criteria`
  - `POST /qe/jira/{ticket}/author?dry_run=false`
  - `POST /qe/jira/{ticket}/sync-results?cucumber_json_path=...`
  - `POST /qe/jira/{ticket}/reconcile?plan_execution_id=...`
  - `GET  /qe/harness/bdd/latest`/{suite}` — run a whole suite (`passed` is the gate)
  - `GET /qe/scenario/{suite}/{id}` — run one scenario (`status` is the gate)
  - `GET /healthz`

A thin **`qe-cli`** (`cli/qe_cli.py`) wraps the deterministic API for CI.

---

## Repository layout

```
qe-agent/
  pyproject.toml
  agent/
    config.py                  # env-driven settings (project, dataset, neo4j, ns…)
    results.py                 # ScenarioResult model
    root_agent.py              # qe_orchestrator (ADK entrypoint)
    sub_agents/                # 5 specialist LlmAgents
    tools/                     # gcs / bigquery / neo4j / kubernetes / dbt toolsets
    scenarios/                 # deterministic checks + runner/registry
    server/main.py             # FastAPI (ADK + /qe endpoints)
  cli/qe_cli.py                # Harness client
  deploy/
    Dockerfile
    cloudbuild.yaml            # Cloud Build config (build + push image)
    k8s/                       # rbac, configmap, secret.example,
                               # deployment, service
.harness/                      # Harness Git Experience pipeline
  orgs/default/projects/QE_HACK/pipelines/
    quality_engineering_hack.yaml   # 5 stages, parallel scenario steps (inline)
```

---

## Test requirements

Each suite below lists **what it requires to run** (preconditions / access) and
**what it asserts** (pass criteria).

### 1. Static code analysis (`static`, SA1–SA5)
- **Requires:** repo checked out at `REPO_ROOT`; `sqlfluff`, `ruff`, `bandit`,
  `yamllint` (bundled in the image).
- **Asserts:**
  - **SA1** dbt SQL passes `sqlfluff` (BigQuery dialect) — 0 violations.
  - **SA2** Python passes `ruff` — 0 issues.
  - **SA3** Python `bandit` — no HIGH/MEDIUM findings.
  - **SA4** all YAML passes `yamllint` — 0 errors.
  - **SA5** no hardcoded secrets/tokens/passwords in YAML/SQL.

### 2. Coding standards (`standards`, CS1–CS5)
- **Requires:** repo at `REPO_ROOT`.
- **Asserts:**
  - **CS1** staging models `stg_*`, marts models `*_enriched`.
  - **CS2** each enriched model declares a `not_null` test on its PK.
  - **CS3** every dbt source table is documented.
  - **CS4** k8s manifests: pinned images, resource limits, `serviceAccountName`,
    `concurrencyPolicy` ∈ {Forbid, Replace}.
  - **CS5** FK column consistently named `customer_id`.

### 3. Integration testing (`integration`, I1–I5)
- **Requires:** read access to GCS, BigQuery and Neo4j; pipeline has run at least
  once.
- **Asserts:**
  - **I1** GCS CSV rows are present in BigQuery raw tables (parity).
  - **I2** enriched tables populated; no orphan `customer_id` FKs.
  - **I3** enriched rows are represented as Neo4j nodes + `HAS_ACCOUNT`/
    `HAS_ADDRESS` relationships.
  - **I4** ingest watermark advances; `processed_files_metadata` has no dupes.
  - **I5** sampled `customer_id` account counts match between BigQuery and Neo4j.

### 4. Functional testing (`functional`, F1–F6)
- **Requires:** read access to BigQuery + Neo4j; ability to run `dbt test`.
- **Asserts the business rules:**
  - **F1** INVESTMENT rule positive — rows matching (sort-code last digit ∈
    {2,3,5,7} **and** last two account-number digits both even) are `INVESTMENT`.
  - **F2** INVESTMENT rule negative — no row is `INVESTMENT` without satisfying
    the predicate.
  - **F3** `full_address == concat(line1, ', ', city, ', ', postcode, ', ', country)`.
  - **F4** `phone_number` is the digits-only form of `phone`.
  - **F5** declared dbt `not_null`/`unique` tests pass (`dbt test`).
  - **F6** Neo4j uniqueness constraints exist; no Account/Address without an
    owning Customer.

### 5. Non-functional testing (`nonfunctional`, N1–N2)
- **Requires:** read access to BigQuery + Kubernetes (pods/jobs/cronjobs/logs).
- **Asserts:**
  - **N1 Performance/SLA** — pipeline Jobs finish within their SLA windows;
    BigQuery probe latency ≤ `BQ_QUERY_SLA_SECONDS`.
  - **N2 Reliability/Security** — cronjobs use safe `concurrencyPolicy`, are not
    suspended; pipeline pod logs free of `ERROR`/`Traceback`/`FATAL`; pipeline
    pods have a bound service account.

SLA thresholds are configurable via `DBT_JOB_SLA_SECONDS`, `INGEST_SLA_SECONDS`,
`BQ_QUERY_SLA_SECONDS`.

---

## IAM — service account & roles

The agent **reuses the shared `qe-hack-syndicate-k8s-sa` service account** — the
same Kubernetes SA used by the dbt cronjobs, `neo4j-ingest` and the synthetic
data generator. It authenticates to GCP via that SA's existing **Workload
Identity** binding (no impersonation hop, no JSON keys in-cluster). Override via
env if your setup differs.

| Identity | Value |
|----------|-------|
| Kubernetes SA | `qe-hack-syndicate-k8s-sa` (ns `qe-hack-syndicate`) — shared with the other workloads |
| GCP SA (via Workload Identity) | `qe-hack-syndicate-svc@project-61358164-b71e-4422-a5c.iam.gserviceaccount.com` |

**Project IAM roles the shared GCP SA needs (least privilege, read-only + Vertex).**
The data-pipeline roles are typically already present; ensure Vertex access for
the agent's Gemini calls:

| Role | Why |
|------|-----|
| `roles/storage.objectViewer` | read raw CSVs in `qe_hack_syndicate_raw` |
| `roles/bigquery.dataViewer`  | read raw/enriched/watermark tables |
| `roles/bigquery.jobUser`     | run read-only SELECT query jobs |
| `roles/aiplatform.user`      | call Gemini on Vertex AI |

**Kubernetes RBAC (namespace `qe-hack-syndicate`, see `deploy/k8s/rbac.yaml`):**
a Role + RoleBinding grant `qe-hack-syndicate-k8s-sa` `get/list` on `pods`,
`pods/log`, `jobs`, `cronjobs`; plus `create` on `jobs` only (to trigger an
ephemeral `dbt test`). No data-mutation permissions.

> The agent never mutates pipeline data. Its only "write" actions are running
> `dbt test` and (optionally) creating a short-lived test Job.

---

## Prerequisites

- GKE cluster with **Workload Identity enabled**, kubectl context set.
- An Artifact Registry repo (e.g. `us-central1-docker.pkg.dev/.../qe`).
- Neo4j reachable in-cluster. The agent defaults to the Service DNS
  `bolt://neo4j.qe-hack-syndicate.svc.cluster.local:7687`. If you only have the
  external `bolt://34.69.104.67:7687`, set `NEO4J_URI` accordingly in the
  ConfigMap.
- `gcloud` + `kubectl`. Docker is **not** required locally — the image is built
  with Cloud Build (handy behind a corporate proxy where local `docker push`
  fails with `connection refused`).

---

## Deploy to Kubernetes

### 1. Build & push the image (Cloud Build)

Builds and pushes from Google's side via Cloud Build — no local Docker daemon,
so it sidesteps corporate-proxy egress issues (`dial tcp ...: connection
refused`). The Dockerfile lives at `deploy/Dockerfile`, so we use the
`deploy/cloudbuild.yaml` config rather than a bare `--tag`.

```bash
cd qe-agent
PROJECT=project-61358164-b71e-4422-a5c
IMAGE=us-central1-docker.pkg.dev/$PROJECT/qe/qe-quality-agent:0.1.0

# One-time: enable APIs and create the Artifact Registry repo.
gcloud services enable cloudbuild.googleapis.com artifactregistry.googleapis.com \
  --project "$PROJECT"
gcloud artifacts repositories create qe \
  --repository-format=docker --location=us-central1 \
  --project "$PROJECT" 2>/dev/null || true

# Build + push from the qe-agent/ context.
gcloud builds submit --config deploy/cloudbuild.yaml \
  --substitutions _IMAGE="$IMAGE" \
  --project "$PROJECT" .
```

> Prefer a local build instead? `docker build -f deploy/Dockerfile -t "$IMAGE" .`
> then `docker push "$IMAGE"` — but behind a proxy you must configure the Docker
> daemon's proxy (`~/.docker/config.json`), which Cloud Build avoids entirely.

### 2. Ensure the shared GCP SA has the required roles

The agent uses the existing `qe-hack-syndicate-k8s-sa` KSA and its Workload
Identity-bound GCP SA. The storage/BigQuery roles are usually already granted to
that SA for the pipeline; just make sure Vertex AI access is present:

```bash
PROJECT=project-61358164-b71e-4422-a5c
GSA=qe-hack-syndicate-svc@$PROJECT.iam.gserviceaccount.com

for ROLE in roles/storage.objectViewer roles/bigquery.dataViewer \
            roles/bigquery.jobUser roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:$GSA" --role "$ROLE"
done
```

> No new service account or Workload Identity binding is required — the shared
> `qe-hack-syndicate-k8s-sa` already maps to this GCP SA, exactly like the dbt
> jobs and the synthetic data generator. `IMPERSONATE_SERVICE_ACCOUNT` is left
> empty so the agent uses that ambient identity directly.

### 3. Create the Neo4j secret

```bash
kubectl create secret generic qe-quality-agent-secrets \
  --namespace qe-hack-syndicate \
  --from-literal=NEO4J_PASSWORD='<neo4j-password>'
```

> The agent's `git-clone` init container also needs the existing
> `git-credentials` secret (key `token`) — the same one the dbt cronjobs use — to
> clone the repo source into `REPO_ROOT` for the static-analysis, coding-standards
> and dbt suites. It already exists in the namespace; create it only if missing:
>
> ```bash
> kubectl create secret generic git-credentials \
>   --namespace qe-hack-syndicate \
>   --from-literal=token='<github-pat>'
> ```

### 4. Apply config + workload

```bash
# Render the image into the Deployment and apply (works on Linux/Cloud Shell and
# macOS; does not modify the tracked manifest).
sed "s#REPLACE_WITH_IMAGE#$IMAGE#" deploy/k8s/deployment.yaml | kubectl apply -f -

kubectl apply -f deploy/k8s/rbac.yaml
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/service.yaml

kubectl -n qe-hack-syndicate rollout status deploy/qe-quality-agent
```

The Deployment references the shared `qe-hack-syndicate-k8s-sa` service account,
so no ServiceAccount manifest is applied here — it already exists in the
namespace.

### 5. Smoke test

```bash
kubectl -n qe-hack-syndicate port-forward svc/qe-quality-agent 8080:8080 &
curl -s localhost:8080/healthz
curl -s localhost:8080/qe/scenarios | head
curl -s localhost:8080/qe/scenario/functional/F3
```

---

## Local development

```bash
cd qe-agent
pip install -e ".[dev]"

# Deterministic API locally:
python -m agent.server.main           # http://localhost:8080

# Interactive ADK chat with the orchestrator:
adk run agent                         # or: adk web
```

Required local env: `GOOGLE_GENAI_USE_VERTEXAI=1`, `GOOGLE_CLOUD_PROJECT`,
`GOOGLE_CLOUD_LOCATION`, ADC via `gcloud auth application-default login`.

---

## CI/CD — Harness

The pipeline lives in the **Harness Git Experience** layout at
`.harness/orgs/default/projects/QE_HACK/pipelines/quality_engineering_hack.yaml`.
It defines a **QE Quality Gate** with **5 stages** (static → standards →
integration → functional → non-functional). Each stage's execution is a
`parallel` block with **one step per scenario**, so all scenarios in a goal run
concurrently (23 scenarios total).

Each step is **self-contained**: an inline Bash script runs `onDelegate` and
`curl`s the agent's deterministic endpoint
(`<+pipeline.variables.QE_AGENT_URL>/qe/scenario/<suite>/<id>`), then `grep`s the
JSON for `"status": "PASS"` and exits non-zero otherwise — gating the pipeline.
No checked-out helper script is needed on the delegate.

To use it:
1. Ensure the Harness delegate runs in the `qe-hack-syndicate` namespace (so it
   reaches the agent at `http://qe-quality-agent.qe-hack-syndicate.svc.cluster.local:8080`).
2. Connect the repo via Git Experience (the `.harness/` tree is auto-discovered)
   or import the pipeline YAML directly.

Override the target with the `QE_AGENT_URL` pipeline variable.
