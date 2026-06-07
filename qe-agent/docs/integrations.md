# GitHub <-> Jira <-> QE Agent <-> Harness on GKE — Integration Guide

This document describes how the four systems collaborate around the
`syndicate-core-engine` repository, where each piece runs, and exactly which
credentials, env vars and Kubernetes objects you need to wire it up.

---

## High-level architecture

```mermaid
flowchart LR
    subgraph Jira[Atlassian Jira Cloud]
      J1[Story / Task<br/>SYN-123<br/>Acceptance Criteria]
      J2[Linked Test issues<br/>SYN-1xx, SYN-1xx]
      J3[Xray / Test execution<br/>history]
      JW[Jira Webhook<br/>issue_created + issue_updated]
    end

    subgraph GitHub[GitHub]
      G1[(syndicate-insights/<br/>syndicate-core-engine)]
      G2[bdd-tests/.../*.feature]
      G3[PR auto-opened by agent]
    end

    subgraph GKE[GKE — namespace qe-hack-syndicate]
      I[Ingress<br/>qe-agent.syndicate-insights.com]
      A[QE Quality Agent<br/>Deployment + Service]
      A1[bdd_authoring_agent]
      A2[/qe/jira/webhook listener/]
      A3[other QE sub-agents]
    end

    subgraph Harness[Harness NextGen]
      HQ[Pipeline:<br/>quality_engineering_hack]
      H1[Pipeline:<br/>bdd_tests<br/>chained from above]
      HT[Custom Webhook trigger<br/>jira_testing_transition]
      H3[Publish JUnit + Cucumber]
    end

    J1 -- webhook: issue_created --> JW
    JW -- HTTPS / token --> I --> A2
    A2 -- author + create Test issues --> A1
    A1 -- create Test issues + link --> J2
    A1 -- branch + commit --> G2
    A1 -- open PR --> G3
    G3 -- merge to main --> G1

    J1 -- webhook: status -> Testing --> JW
    A2 -- POST Custom Webhook --> HT --> H1

    HQ -- final stage chains --> H1
    H1 --> A
    H1 -- /qe/scenario/* --> A
    H3 -- /qe/jira/{ticket}/sync-results --> J3
    H3 -- failure --> A1
   qe-quality-agent` Ingress | GKE, GCE-managed cert | Exposes `https://qe-agent.syndicate-insights.com/qe/jira/webhook` (path-restricted) so Jira Cloud can call the agent. |
| `quality_engineering_hack` Harness pipeline | Harness NextGen, in-cluster delegate | Five quality stages (static / standards / integration / functional / non-functional) followed by a chained **BDD Tests** stage. |
| `bdd_tests` Harness pipeline | Same project, chained from above | Runs `mvn -B -ntp test` from `bdd-tests/` against the agent and publishes JUnit + Cucumber reports. |
| Custom Webhook trigger `jira_testing_transition` | Same project | Fired by the agent when a ticket transitions into `Testing`; runs the `bdd_tests` pipeline scoped to that ticket. |
| Jira Cloud | SaaS | Source of truth for acceptance criteria; receives Test issues + per-scenario PASS/FAIL via comments and Xray import. Two webhooks fire into the agent: `issue_created` and `issue_updated`. |
| GitHub | SaaS | Hosts the repo. Receives auto-PRs from the agent (new features for new tickets, refreshed features after failing runs). |

Everything inside GKE talks over cluster DNS:
`http://qe-quality-agent.qe-hack-syndicate.svc.cluster.local:8080`. Only the
`/qe/jira/webhook` and `/healthz` paths are exposed to the public internet
through the Ingress so Jira Cloud can reach them
|---|---|---|
| `qe-quality-agent` Deployment | GKE, namespace `qe-hack-syndicate` | Hosts the deterministic `/qe/...` API and the ADK sub-agents (including `bdd_authoring_agent`). |
| `syndicate_bdd_tests` Harness pipeline | Harness NextGen, GKE delegate in the same cluster | Runs `mvn -B -ntp test` from `bdd-tests/` against the agent's in-cluster Service URL, publishes JUnit + Cucumber JSON, and calls `qe-cli jira sync-results`. |
| Jira Cloud | SaaS | Source of truth for acceptance criteria; receives Test issues + per-scenario PASS/FAIL via comments and Xray import. |
| GitHub | SaaS | Hosts the repo. Receives auto-PRs from the agent (new features for new tickets, refreshed features after failing runs). |

Everything inside GKE talks over cluster DNS:
`http://qe-quality-agent.qe-hack-syndicate.svc.cluster.local:8080`. No public
ingress is required for Harness <-> Agent because the Harness CI delegate runs
in the same cluster and namespace.

---
Ticket created → BDD authored automatically

When a Jira `Story`/`Task`/`Bug` is created, Jira fires `jira:issue_created`
to `https://qe-agent.syndicate-insights.com/qe/jira/webhook?token=...`. The
agent's webhook listener
([`server/jira_webhook.py`](../agent/server/jira_webhook.py)):

1. Verifies the `?token=` query parameter against `JIRA_WEBHOOK_TOKEN`.
2. Calls `bdd_authoring_agent.author_bdd_scenarios(ticket)`, which:
   - reads the AC bullets from the ticket,
   - generates `bdd-tests/src/test/resources/feature/<Domain>/<slug>.feature`,
   - creates one Jira `Test` issue per `Scenario`, linked to the parent via
     `Tests`, and
   - opens a PR against `syndicate-insights/syndicate-core-engine` labelled
     `qe-agent`, `bdd`, `auto-generated`.

You can also run this manually:

```
qe-cli jira author SYN-123          # full run
qe-cli jira author SYN-123 --dry-run    # preview the .feature locally
```

### 2. Ticket moves to "Testing" → Harness BDD pipeline runs

When the ticket transitions into the configured `Testing` status, Jira fires
`jira:issue_updated`. The webhook listener:

1. Detects the status transition in the `changelog`,
2. POSTs to the pre-issued Harness Custom Webhook URL
   (`HARNESS_BDD_WEBHOOK_URL`) with `{"issue": {"key": "<TICKET>"}}`,
3. The trigger
   [`jira_testing_transition`](../../.harness/orgs/default/projects/QE_HACK/triggers/jira_testing_transition.yaml)
   maps `<+trigger.payload.issue.key>` to the pipeline's `jiraTicket`
   variable and starts `bdd_tests`.

The `bdd_tests` pipeline:

1. Clones the repo,
2. Spins up a `maven:3.9.6-eclipse-temurin-21` container in
   `qe-hack-syndicate` and runs `mvn -B -ntp test` from `bdd-tests/`,
3. Each Cucumber step calls
   `http://qe-quality-agent.qe-hack-syndicate.svc.cluster.local:8080/qe/scenario/<suite>/<id>`,
4. JUnit XML is published as a Harness `JUnit` report,
5. The "Publish to Jira / Xray" stage POSTs to
   `/qe/jira/<ticket>/sync-results`, which comments on the parent ticket and
   transitions the linked Test issues (and pushes to Xray Cloud if configured).

### 3. Full quality sweep ends with BDD

The main pipeline
[`quality_engineering_hack`](../../.harness/orgs/default/projects/QE_HACK/pipelines/quality_engineering_hack.yaml)
chains `bdd_tests` as its sixth stage (`type: Pipeline`). A merge to `main`
runs the five deterministic suites and, if they pass, runs the BDD pack so
all Cucumber features are exercised before deployment.

### 4. Reconcile a failing run

When the BDD pipeline fails, the `bdd_authoring_agent` reconciles:

```
qe-cli jira reconcile SYN-123 --execution $HARNESS_BUILD_ID
```

It calls `update_bdd_from_failure`, which reads the latest AC, regenerates the
`.feature` file, and opens a follow-up PR labelled `auto-fix` so a human
reviewer can confirm whether the AC actually shifted. If not, close the PR
and treat the failure   and the Harness execution URL).

If the AC has not changed, the reviewer closes the PR and treats the failure
as a real regression.

---

## Step-by-step setup

### Prerequisites

* GKE cluster with namespace `qe-hack-syndicate` and KSA `qe-hack-syndicate-k8s-sa`
  (already used by the dbt cronjobs).
* Harness NextGen account with a Kubernetes delegate in the same cluster.
* Jira Cloud project (default: `SYN`) with a `Test` issuetype enabled
  (Xray Cloud is optional but recommended).
* GitHub repository `syndicate-insights/syndicate-core-engine`.

### 1. Mint credentials

| Credential | Where created | Scope |
|---|---|---| \
  --from-literal=JIRA_WEBHOOK_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=HARNESS_BDD_WEBHOOK_URL='<paste-from-harness-trigger>'
```

`HARNESS_BDD_WEBHOOK_URL` is the URL Harness shows when you save the
[`jira_testing_transition`](../../.harness/orgs/default/projects/QE_HACK/triggers/jira_testing_transition.yaml)
Custom Webhook trigger; treat the full URL as a secret because anyone with it
can fire the pipeline. Apply [`secret.example.yaml`](../deploy/k8s/secret.example.yaml)
after replacing the placeholders (do **not** commit real values).

### 3. Update the ConfigMap and apply the manifests

Edit [`configmap.yaml`](../deploy/k8s/configmap.yaml) and set:

```
JIRA_BASE_URL=https://<your-tenant>.atlassian.net
JIRA_USER=qe-agent@<your-domain>
JIRA_PROJECT=SYN
JIRA_AC_FIELD=customfield_10100   # only if you use a custom AC field
JIRA_TESTING_STATUS=Testing       # status name that triggers the BDD pipeline
JIRA_TRIGGER_ISSUETYPES=Story,Task,Bug
HARNESS_ORG_ID=default
HARNESS_PROJECT_ID=QE_HACK
HARNESS_BDD_PIPELINE_ID=bdd_tests
GITHUB_REPO=syndicate-insights/syndicate-core-engine
```

Apply:

```bash
kubectl apply -f qe-agent/deploy/k8s/configmap.yaml
kubectl apply -f qe-agent/deploy/k8s/deployment.yaml
kubectl apply -f qe-agent/deploy/k8s/service.yaml
kubectl apply -f qe-agent/deploy/k8s/ingress.yaml      # exposes /qe/jira/webhook
```

The init container in the Deployment clones the repo (read-only) into a shared
emptyDir so the static-analysis and coding-standards suites can lint it.

### 4. Sync the Harness pipelines via Git Experience

Both pipelines and the trigger live in the repo under `.harness/`:

```
.harness/orgs/default/projects/QE_HACK/
  pipelines/
    quality_engineering_hack.yaml   # main pipeline (chains bdd_tests as last stage)
    bdd_tests.yaml                  # Cucumber pack
  triggers/
    jira_testing_transition.yaml    # Custom Webhook the agent fires
```

Connect the Harness project to this Git repo via Git Experience (Account >
Git Experience > New Connection); Harness will pick up changes on every push.
After the trigger is saved, copy its webhook URL into the
`HARNESS_BDD_WEBHOOK_URL` Secret (step 2).

### 5. Configure the Jira webhooks

In Jira: Settings > System > WebHooks > Create:

* **URL:** `https://qe-agent.syndicate-insights.com/qe/jira/webhook?token=<JIRA_WEBHOOK_TOKEN>`
* **Events:** `Issue created`, `Issue updated`
* **JQL filter:** `project = SYN AND issuetype in (Story, Task, Bug)` (adjust
  to the projects you want to manage).
* **Exclude body:** off (the agent needs the issue payload).

Tip: send a test event with `Send test event` and check
`kubectl -n qe-hack-syndicate logs deploy/qe-quality-agent -f` for a 200
response.

### 6. Smoke test

```bash
# Dry-run the BDD authoring locally
Configure the pipeline triggers:

* `On Push to main` — runs the full BDD pack and updates Jira.
* `On PR opened with label qe-agent` — runs the BDD pack against the PR branch
  so the agent's auto-generated tests are validated before merge.

### 5. (Optional) GitHub <-> Harness webhook

Add a GitHub webhook on the `syndicate-core-engine` repo pointing to the
Harness webhook URL emitted by the pipeline trigger, so push and PR events
auto-trigger `syndicate_bdd_tests`.

### 6. Smoke test

```bash
# Author scenarios for an existing Jira ticket (dry-run prints the feature):
QE_AGENT_URL=http://localhost:8080 qe-cli jira author SYN-123 --dry-run

# Real run (creates Test issues + opens a PR):
qe-cli jira author SYN-123

# Trigger the BDD pipeline manually and watch the latest status:
qe-cli harness latest
```

---

## Security notes

* Secrets are mounted as `secretKeyRef` env vars; nothing is baked into the
  container image. The `secret-scan` static-analysis scenario flags any
  hardcoded credential that slips back into the repo.
* The agent's KSA (`qe-hack-syndicate-k8s-sa`) only has read access to the
  pipeline's BigQuery/GCS/Neo4j; PR creation relies on the GitHub token, not
  on cluster identity, so revoking GitHub access is a single PAT rotation.
* The agent never executes cluster-mutating commands; `dbt test` is the only
  side effect it runs, and Cucumber tests are read-only HTTP calls to the
  agent's own API.
* Harness NextGen runs the BDD pipeline with a delegate inside the cluster,
  so the agent's HTTP API never needs a public ingress.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `qe-cli jira author` returns `error 401` | Bad/expired Jira API token | Rotate the token; re-create the Secret. |
| `pr` field is `null` after `author` | `GITHUB_TOKEN` lacks `pull-requests:write` | Recreate the PAT with the right scopes. |
| Jira Test issues not linked | `Tests` issue link type missing in the project | Ask the Jira admin to enable it (Xray installs it by default). |
| Harness step "Publish to Jira / Xray" fails | `cucumber.json` missing | Make sure `mvn test` ran the JUnit XML/JSON plugins (set in `pom.xml`). |
| BDD pipeline can't reach the agent | DNS or Service mismatch | `kubectl -n qe-hack-syndicate get svc qe-quality-agent` and check `QE_AGENT_URL`. |
