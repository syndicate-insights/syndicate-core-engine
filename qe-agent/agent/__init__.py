"""QE Quality Agent package.

Exposes `root_agent` so ADK tooling (`adk run`, `adk web`, API server) can
discover the orchestrator. The import is guarded so the deterministic scenario
suites and the CLI remain importable in minimal environments where the
`google-adk` / Vertex stack is not installed (e.g. lint-only CI images).
"""

try:  # pragma: no cover - exercised only when google-adk is installed
    from agent.root_agent import root_agent

    __all__ = ["root_agent"]
except ModuleNotFoundError:  # ADK / Gemini stack not available
    __all__ = []
