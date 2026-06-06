# QE Quality Agent — container image
# Multi-purpose: serves the ADK API + deterministic /qe endpoints, and ships the
# qe-cli used by Harness steps. dbt-bigquery is included so the agent can run
# `dbt test` against the three transform projects.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    GOOGLE_GENAI_USE_VERTEXAI=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy project metadata + source before install: `pip install .` builds a wheel
# from this directory, so the package dirs and README referenced by
# pyproject.toml must already be present.
COPY pyproject.toml README.md ./
COPY agent ./agent
COPY cli ./cli

RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir "dbt-bigquery==1.8.*"

# Non-root runtime.
RUN useradd --create-home --uid 10001 qeagent
USER 10001

EXPOSE 8080

# Default: run the combined ADK + QE FastAPI server.
CMD ["python", "-m", "agent.server.main"]
