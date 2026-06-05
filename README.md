# syndicate-core-engine

Data pipeline: synthetic data → GCS → dbt/BigQuery → Neo4j, running on GKE in the
`qe-hack-syndicate` namespace.

## Quality Engineering AI Agent

A Google ADK + Gemini Quality Engineering agent for this pipeline lives in
[`qe-agent/`](qe-agent/README.md). It provides static analysis, coding-standard
checks, and integration / functional / non-functional testing, and is invoked by
the Harness pipeline in [`harness/`](harness/pipeline.yaml).

See [qe-agent/README.md](qe-agent/README.md) for architecture, test
requirements, IAM roles, and Kubernetes deployment instructions.
