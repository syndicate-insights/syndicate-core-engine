# syndicate-core-engine

Data pipeline: synthetic data → GCS → dbt/BigQuery → Neo4j, running on GKE in the
`qe-hack-syndicate` namespace.

## Quality Engineering AI Agent

A Google ADK + Gemini Quality Engineering agent for this pipeline lives in
[`qe-agent/`](qe-agent/README.md). It provides static analysis, coding-standard
checks, integration / functional / non-functional testing, **and** a BDD
authoring sub-agent that turns Jira acceptance criteria into Cucumber tests
and reconciles failing Harness runs.

It is invoked by the Harness pipeline at
[`.harness/orgs/default/projects/QE_HACK/pipelines/quality_engineering_hack.yaml`](.harness/orgs/default/projects/QE_HACK/pipelines/quality_engineering_hack.yaml).

## BDD test pack

The Cucumber BDD test pack lives in [`bdd-tests/`](bdd-tests/README.md). Every
`.feature` file delegates to the QE agent's deterministic `/qe/scenario/...`
API so the same code path runs in the agent, in `qe-cli`, and in CI. The
Harness pipeline that runs the pack is in
[`bdd-tests/harness/bdd-pipeline.yaml`](bdd-tests/harness/bdd-pipeline.yaml).

## Integration documentation

For the full GitHub <-> Jira <-> Agent <-> Harness wiring on GKE see
[`qe-agent/docs/integrations.md`](qe-agent/docs/integrations.md).

See [qe-agent/README.md](qe-agent/README.md) for architecture, test
requirements, IAM roles, and Kubernetes deployment instructions.
