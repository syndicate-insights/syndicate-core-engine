"""Centralised, color-coded observability for the QE Quality Agent.

Goals:
  * One place that configures logging for the whole process (server, root
    agent, every sub-agent, every toolset and the underlying ADK / Gemini
    libraries) so *everything* shows up at the pod level (`kubectl logs`).
  * Color-coded output so each kind of action is visually distinct:
      - agent lifecycle (enter/exit)      -> magenta
      - model calls (source -> model)     -> blue
      - tool / function calls             -> cyan
      - results / responses               -> green
      - warnings                          -> yellow
      - errors                            -> red
  * ADK callbacks that log every model request, every tool/function call, the
    model being used, the source (calling agent) and destination (model or
    tool), arguments, responses and token usage.

Nothing here hard-depends on `google-adk`; the callbacks duck-type the ADK
objects via ``getattr`` so this module also imports cleanly in minimal images.

Environment toggles:
  LOG_LEVEL   : root log level (default INFO; set DEBUG to see payloads).
  LOG_COLOR   : "1"/"true" to force ANSI colors (default on), "0" to disable.
  LOG_MAX_LEN : max characters per logged payload before truncation (default 2000).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

# --- ANSI color helpers -----------------------------------------------------

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"
_GREY = "\033[90m"
_BRIGHT_RED = "\033[91m"


def _color_enabled() -> bool:
    raw = os.environ.get("LOG_COLOR", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    # Default: on. Pod logs aren't a TTY but kubectl/most viewers render ANSI,
    # and the operator explicitly asked for color-coded logs.
    return True


_COLOR = _color_enabled()


def c(text: Any, color: str) -> str:
    """Wrap text in an ANSI color (no-op when color is disabled)."""
    if not _COLOR:
        return str(text)
    return f"{color}{text}{_RESET}"


def _max_len() -> int:
    try:
        return int(os.environ.get("LOG_MAX_LEN", "2000"))
    except ValueError:
        return 2000


def short(obj: Any, limit: int | None = None) -> str:
    """JSON-ish stringify with truncation so big payloads stay readable."""
    limit = limit or _max_len()
    try:
        s = obj if isinstance(obj, str) else json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        s = repr(obj)
    if len(s) > limit:
        return s[:limit] + c(f"...(+{len(s) - limit} more chars)", _DIM)
    return s


# --- Formatter --------------------------------------------------------------

_LEVEL_COLORS = {
    logging.DEBUG: _GREY,
    logging.INFO: _GREEN,
    logging.WARNING: _YELLOW,
    logging.ERROR: _RED,
    logging.CRITICAL: _BOLD + _BRIGHT_RED,
}


class ColorFormatter(logging.Formatter):
    """Formatter that color-codes the level and logger name."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        if _COLOR:
            level_color = _LEVEL_COLORS.get(record.levelno, "")
            record.levelname = f"{level_color}{record.levelname:<8}{_RESET}"
            record.name = c(record.name, _CYAN if record.name.startswith("agent") else _GREY)
        return super().format(record)


# --- Configuration ----------------------------------------------------------

_CONFIGURED = False


def configure_logging() -> None:
    """Install the color formatter on the root logger (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColorFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    # Replace any pre-existing handlers (e.g. uvicorn / basicConfig) so output
    # is consistent and color-coded everywhere.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    # Make the noisy-but-useful libraries flow through our handler too, so we
    # capture the model calls, transfers and HTTP traffic the ADK stack makes.
    for name in (
        "agent",
        "google_adk",
        "google.adk",
        "google.genai",
        "google_genai",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "httpx",
        "httpcore",
    ):
        lib = logging.getLogger(name)
        lib.setLevel(level)
        lib.propagate = True
        for h in list(lib.handlers):
            lib.removeHandler(h)

    _CONFIGURED = True
    logging.getLogger("agent.observability").info(
        "%s logging configured: level=%s color=%s",
        c("✓", _GREEN), level, _COLOR,
    )


# --- ADK callbacks (the "log everything the agent does" layer) ---------------

_trace = logging.getLogger("agent.trace")


def _ctx_agent(ctx: Any) -> str:
    return str(getattr(ctx, "agent_name", None) or getattr(ctx, "name", None) or "?")


def _ctx_inv(ctx: Any) -> str:
    return str(getattr(ctx, "invocation_id", None) or "-")


def _before_agent(callback_context: Any = None, **_: Any):
    name = _ctx_agent(callback_context)
    _trace.info(
        "%s %s agent=%s inv=%s",
        c("▶ ENTER", _MAGENTA + _BOLD),
        c("agent", _MAGENTA),
        c(name, _MAGENTA + _BOLD),
        _ctx_inv(callback_context),
    )
    return None


def _after_agent(callback_context: Any = None, **_: Any):
    name = _ctx_agent(callback_context)
    _trace.info(
        "%s %s agent=%s inv=%s",
        c("■ EXIT ", _MAGENTA + _BOLD),
        c("agent", _MAGENTA),
        c(name, _MAGENTA + _BOLD),
        _ctx_inv(callback_context),
    )
    return None


def _before_model(callback_context: Any = None, llm_request: Any = None, **_: Any):
    source = _ctx_agent(callback_context)
    model = getattr(llm_request, "model", None) or "?"
    contents = getattr(llm_request, "contents", None)
    msg_count = len(contents) if isinstance(contents, (list, tuple)) else "?"
    _trace.info(
        "%s %s %s %s  (messages=%s) inv=%s",
        c("→ MODEL", _BLUE + _BOLD),
        c(source, _MAGENTA),
        c("⇒", _DIM),
        c(model, _BLUE + _BOLD),
        msg_count,
        _ctx_inv(callback_context),
    )
    if _trace.isEnabledFor(logging.DEBUG) and contents is not None:
        _trace.debug("%s request=%s", c("  ↳", _BLUE), short(_safe(contents)))
    return None


def _after_model(callback_context: Any = None, llm_response: Any = None, **_: Any):
    source = _ctx_agent(callback_context)
    usage = getattr(llm_response, "usage_metadata", None)
    tokens = ""
    if usage is not None:
        tokens = (
            f" tokens(prompt={getattr(usage, 'prompt_token_count', '?')}"
            f" out={getattr(usage, 'candidates_token_count', '?')}"
            f" total={getattr(usage, 'total_token_count', '?')})"
        )
    # Surface any function calls the model decided to make.
    calls = _function_calls(llm_response)
    call_str = ""
    if calls:
        call_str = " " + c("calls=" + ",".join(calls), _CYAN + _BOLD)
    _trace.info(
        "%s %s%s%s inv=%s",
        c("← MODEL", _GREEN + _BOLD),
        c(source, _MAGENTA),
        c(tokens, _DIM),
        call_str,
        _ctx_inv(callback_context),
    )
    if _trace.isEnabledFor(logging.DEBUG):
        text = _response_text(llm_response)
        if text:
            _trace.debug("%s response=%s", c("  ↳", _GREEN), short(text))
    return None


def _before_tool(tool: Any = None, args: Any = None, tool_context: Any = None, **_: Any):
    tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", None) or "?"
    source = _ctx_agent(tool_context)
    _trace.info(
        "%s %s %s %s args=%s inv=%s",
        c("⚙ CALL ", _CYAN + _BOLD),
        c(source, _MAGENTA),
        c("⇒", _DIM),
        c(tool_name, _CYAN + _BOLD),
        short(args),
        _ctx_inv(tool_context),
    )
    return None


def _after_tool(tool: Any = None, args: Any = None, tool_context: Any = None,
                tool_response: Any = None, **_: Any):
    tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", None) or "?"
    source = _ctx_agent(tool_context)
    is_error = isinstance(tool_response, dict) and "error" in tool_response
    head = c("✗ DONE ", _RED + _BOLD) if is_error else c("✓ DONE ", _GREEN + _BOLD)
    level = logging.ERROR if is_error else logging.INFO
    _trace.log(
        level,
        "%s %s %s %s -> %s inv=%s",
        head,
        c(source, _MAGENTA),
        c("⇒", _DIM),
        c(tool_name, _CYAN + _BOLD),
        short(tool_response),
        _ctx_inv(tool_context),
    )
    return None


def agent_callbacks() -> dict[str, Any]:
    """Return the kwargs to splat into an ``LlmAgent(...)`` constructor so it
    emits full, color-coded traces of its lifecycle, model and tool calls."""
    return {
        "before_agent_callback": _before_agent,
        "after_agent_callback": _after_agent,
        "before_model_callback": _before_model,
        "after_model_callback": _after_model,
        "before_tool_callback": _before_tool,
        "after_tool_callback": _after_tool,
    }


# --- Small defensive extractors for ADK / genai response shapes -------------

def _safe(contents: Any) -> Any:
    """Reduce ADK/genai content objects to plain text for logging."""
    out = []
    try:
        for item in contents:
            role = getattr(item, "role", None)
            parts = getattr(item, "parts", None) or []
            texts = [getattr(p, "text", None) for p in parts]
            texts = [t for t in texts if t]
            out.append({"role": role, "text": " ".join(texts)[:500]})
    except Exception:  # noqa: BLE001
        return contents
    return out


def _function_calls(llm_response: Any) -> list[str]:
    names: list[str] = []
    try:
        content = getattr(llm_response, "content", None)
        parts = getattr(content, "parts", None) or []
        for p in parts:
            fc = getattr(p, "function_call", None)
            if fc is not None and getattr(fc, "name", None):
                names.append(fc.name)
    except Exception:  # noqa: BLE001
        pass
    return names


def _response_text(llm_response: Any) -> str:
    try:
        content = getattr(llm_response, "content", None)
        parts = getattr(content, "parts", None) or []
        texts = [getattr(p, "text", None) for p in parts]
        return " ".join(t for t in texts if t)
    except Exception:  # noqa: BLE001
        return ""
