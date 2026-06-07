# syndicate-qe-bdd

Cucumber BDD test pack for the [`syndicate-core-engine`](../README.md) data pipeline.

The features are intentionally thin: every step delegates to the **QE Quality
Agent** (`qe-agent/`) running in GKE, so the deterministic Python scenarios stay
the single source of truth. The same code path that Harness CI already gates on
is now expressed in Gherkin, which makes it easy to:

* link each scenario to a Jira Test issue,
* attach JUnit / Cucumber reports back to Harness,
* let the QE agent author new features from a Jira ticket's acceptance criteria.

## Layout

```
bdd-tests/
  pom.xml
  k8s/bdd-tests-job.yaml             # Kubernetes Job fallback (manual run)
  src/test/java/com/syndicate/qe/bdd/
    runner/BddRunnerTest.java        # JUnit 5 + Cucumber suite launcher
    steps/QeScenarioSteps.java       # Generic step defs (delegate to QE agent)
    support/QeAgentClient.java       # Tiny HTTP client for /qe/...
  src/test/resources/
    application.properties
    cucumber.properties
    feature/
      StaticAnalysis/StaticAnalysis.feature
      CodingStandards/CodingStandards.feature
      Integration/Integration.feature
      Functional/Functional.feature
      NonFunctional/NonFunctional.feature
```

The Harness pipeline that runs this pack lives — alongside every other Harness
pipeline — under
[`.harness/orgs/default/projects/QE_HACK/pipelines/bdd_tests.yaml`](../.harness/orgs/default/projects/QE_HACK/pipelines/bdd_tests.yaml),
and is chained as the final stage of
[`quality_engineering_hack`](../.harness/orgs/default/projects/QE_HACK/pipelines/quality_engineering_hack.yaml).
The matching webhook trigger is at
[`.harness/.../triggers/jira_testing_transition.yaml`](../.harness/orgs/default/projects/QE_HACK/triggers/jira_testing_transition.yaml).

The folder layout (`src/test/resources/feature/<Domain>/<Feature>.feature`)
mirrors the reference framework
[`ea76bf-ecp-soi-quality-engineering-bdd-framework`](https://github.com/) so
engineers familiar with that repo are immediately productive.

## Running locally

```bash
# point at a port-forwarded agent if you don't have cluster DNS:
kubectl -n qe-hack-syndicate port-forward svc/qe-quality-agent 8080:8080 &
export QE_AGENT_URL=http://localhost:8080

cd bdd-tests
mvn -B -ntp test                                 # all features
mvn -B -ntp test -Dcucumber.filter.tags=@Functional   # one suite
```

Reports are written to `bdd-tests/target/`:
The pipeline at
[`.harness/.../pipelines/bdd_tests.yaml`](../.harness/orgs/default/projects/QE_HACK/pipelines/bdd_tests.yaml)
defines a `CI` stage that:

1. Clones the repo,
2. Runs `mvn test` against the in-cluster QE agent
   (`qe-quality-agent.qe-hack-syndicate.svc.cluster.local`),
3. Publishes JUnit results to Harness,
4. When `jiraTicket` is provided, calls
   `POST /qe/jira/<ticket>/sync-results` to update the linked Jira Test issues
   with the per-scenario PASS/FAIL status and a link back to the Harness execution.

The pipeline is invoked in three ways:

* **Chained** as the last stage of `quality_engineering_hack` so a full
  quality sweep ends with the BDD pack.
* **Custom Webhook trigger** fired by the QE agent when a Jira ticket
  transitions into the `Testing` status (see
  [`triggers/jira_testing_transition.yaml`](../.harness/orgs/default/projects/QE_HACK/triggers/jira_testing_transition.yaml)).
* **Manually** via the Pipeline Studio with the `jiraTicket` runtime input

1. Clones the repo,
2. Runs `mvn test` against the in-cluster QE agent
   (`qe-quality-agent.qe-hack-syndicate.svc.cluster.local`),
3. Publishes JUnit results to Harness,
4. Calls `qe-cli jira sync-results` to update the linked Jira Test issues with
   the per-scenario PASS/FAIL status and a link back to the Harness execution.

See [`qe-agent/docs/integrations.md`](../qe-agent/docs/integrations.md) for the
end-to-end GitHub <-> Jira <-> Agent <-> Harness wiring on GKE.

## Adding a new feature from a Jira ticket

The QE agent's `bdd_authoring_agent` does this for you:

```bash
qe-cli jira author --ticket SYN-123
```

It reads the ticket's acceptance criteria, generates a `.feature` file under
`bdd-tests/src/test/resources/feature/<Domain>/`, creates Jira Test issues for
each `Scenario`, links them to `SYN-123`, and opens a PR against this repo.
