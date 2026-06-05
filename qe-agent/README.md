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
└── non_functional_test_agent  ── nonfunctional suite

Toolsets (FunctionTools, impersonated GCP SA + WI):
  gcs_toolset · bigquery_toolset · neo4j_toolset · kubernetes_toolset · dbt_toolset
```

Surfaces (one process, port `8080`):

- **ADK agent API** (`/run`, `/run_sse`, sessions) — LLM-driven sweeps & triage.
- **Deterministic QE API** — what Harness calls:
  - `GET /qe/scenarios` — list suites + scenario ids
  - `GET /qe/suite/{suite}` — run a whole suite (`passed` is the gate)
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
    k8s/                       # serviceaccount, rbac, configmap, secret.example,
                               # deployment, service
harness/
  pipeline.yaml                # 5 stages, parallel scenario steps
  scripts/run_scenario.sh      # curl + gate helper used by each step
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

The agent **impersonates a dedicated GCP service account** via **Workload
Identity** (no JSON keys in-cluster). All values default to the discovered
project; override via env if different.

| Identity | Value |
|----------|-------|
| Kubernetes SA | `qe-quality-agent-sa` (ns `qe-hack-syndicate`) |
| GCP SA | `qe-quality-agent-svc@project-61358164-b71e-4422-a5c.iam.gserviceaccount.com` |

**Project IAM roles to grant the GCP SA (least privilege, read-only + Vertex):**

| Role | Why |
|------|-----|
| `roles/storage.objectViewer` | read raw CSVs in `qe_hack_syndicate_raw` |
| `roles/bigquery.dataViewer`  | read raw/enriched/watermark tables |
| `roles/bigquery.jobUser`     | run read-only SELECT query jobs |
| `roles/aiplatform.user`      | call Gemini on Vertex AI |

**Kubernetes RBAC (namespace `qe-hack-syndicate`, see `deploy/k8s/rbac.yaml`):**
`get/list` on `pods`, `pods/log`, `jobs`, `cronjobs`; plus `create` on `jobs`
only (to trigger an ephemeral `dbt test`). No data-mutation permissions.

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
- `gcloud`, `kubectl`, `docker` installed locally.

---

## Deploy to Kubernetes

### 1. Build & push the image

```bash
cd qe-agent
PROJECT=project-61358164-b71e-4422-a5c
IMAGE=us-central1-docker.pkg.dev/$PROJECT/qe/qe-quality-agent:0.1.0

docker build -f deploy/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"
```

### 2. Create the GCP service account & grant roles

```bash
PROJECT=project-61358164-b71e-4422-a5c
GSA=qe-quality-agent-svc@$PROJECT.iam.gserviceaccount.com

gcloud iam service-accounts create qe-quality-agent-svc \
  --project "$PROJECT" --display-name "QE Quality Agent"

for ROLE in roles/storage.objectViewer roles/bigquery.dataViewer \
            roles/bigquery.jobUser roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:$GSA" --role "$ROLE"
done
```

### 3. Bind Workload Identity (KSA ⇄ GSA)

```bash
PROJECT=project-61358164-b71e-4422-a5c
GSA=qe-quality-agent-svc@$PROJECT.iam.gserviceaccount.com
NS=qe-hack-syndicate
KSA=qe-quality-agent-sa

# Apply the KSA first (carries the iam.gke.io annotation).
kubectl apply -f deploy/k8s/serviceaccount.yaml

gcloud iam service-accounts add-iam-policy-binding "$GSA" \
  --project "$PROJECT" \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:$PROJECT.svc.id.goog[$NS/$KSA]"
```

> If you keep impersonation (`IMPERSONATE_SERVICE_ACCOUNT` set), also grant the
> KSA's bound principal `roles/iam.serviceAccountTokenCreator` on the GSA. If WI
> maps the KSA **directly** to the GSA, leave `IMPERSONATE_SERVICE_ACCOUNT`
> empty and skip token-creator.

### 4. Create the Neo4j secret

```bash
kubectl create secret generic qe-quality-agent-secrets \
  --namespace qe-hack-syndicate \
  --from-literal=NEO4J_PASSWORD='<neo4j-password>'
```

### 5. Apply config + workload

```bash
# Set the image in the Deployment (replace placeholder).
sed -i '' "s#REPLACE_WITH_IMAGE#$IMAGE#" deploy/k8s/deployment.yaml   # macOS sed

kubectl apply -f deploy/k8s/rbac.yaml
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/deployment.yaml

kubectl -n qe-hack-syndicate rollout status deploy/qe-quality-agent
```

### 6. Smoke test

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

`harness/pipeline.yaml` defines a **QE Quality Gate** pipeline with **5 stages**
(static → standards → integration → functional → non-functional). Each stage's
execution is a `parallel` block with **one step per scenario**, so all scenarios
in a goal run concurrently.

Each step runs `harness/scripts/run_scenario.sh <suite> <id>` on the **delegate**
(same namespace as the agent), which `curl`s
`http://qe-quality-agent.qe-hack-syndicate.svc.cluster.local:8080` and exits
non-zero on a non-`PASS` status — gating the pipeline.

To use it:
1. Ensure the Harness delegate runs in `qe-hack-syndicate`.
2. Import `harness/pipeline.yaml` (replace `<ORG>`/`<PROJECT>`), ship
   `harness/scripts/run_scenario.sh` with the codebase, and run.

Override the target with the pipeline variable / env `QE_AGENT_URL`.














## What was built

**ADK agent** (qe-agent, Python + Gemini on Vertex AI)
- Root orchestrator agent/root_agent.py coordinating **5 sub-agents** (one per goal) in agent/sub_agents/agents.py.
- **5 toolsets** in agent/tools/: GCS, BigQuery, Neo4j, Kubernetes (pods/jobs/cronjobs/logs), dbt — all read-only except triggering `dbt test`. Impersonation via Workload Identity in credentials.py.
- **Deterministic scenarios** in agent/scenarios/: static (SA1–SA5), standards (CS1–CS5), integration (I1–I5), functional (F1–F6), non-functional (N1–N2). Functional checks encode the actual business rules (INVESTMENT reclassification, `full_address`, phone digits-only).
- FastAPI server agent/server/main.py serving ADK endpoints + deterministic `/qe/...` gate endpoints; thin CLI cli/qe_cli.py.

**Deploy** (qe-agent/deploy/): Dockerfile + K8s manifests (SA w/ WI annotation, least-privilege RBAC, ConfigMap, Secret example, Deployment, ClusterIP Service).

**IAM/roles** (documented in README.md): GCP SA roles `storage.objectViewer`, `bigquery.dataViewer`, `bigquery.jobUser`, `aiplatform.user`; namespace RBAC get/list pods/logs/jobs/cronjobs + create jobs; Workload Identity binding steps.

**Harness CICD** (harness/): pipeline.yaml with **5 sequential stages**, each a `parallel` block of per-scenario steps calling scripts/run_scenario.sh against the in-namespace Service DNS, gating on JSON `status`.

**Verification**: `compileall` clean, `pytest` 4/4 green (real repo), CLI runs standalone, pipeline YAML parses.

Three things you'll set at deploy time: the image ref (`REPLACE_WITH_IMAGE` in the Deployment + `sed` step in README), the Neo4j password Secret, and `<ORG>`/`<PROJECT>` in the Harness pipeline. If your Neo4j has no in-cluster Service, point `NEO4J_URI` in the ConfigMap at the external `bolt://34.69.104.67:7687`.

Want me to add a `neo4j` ClusterIP/headless Service manifest so the default in-cluster `NEO4J_URI` resolves, or wire a Vertex AI Gemini smoke check into the readiness path?