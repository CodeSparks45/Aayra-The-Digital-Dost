"""
app/utils/logger.py

Structured, leveled logging for Aayra - Digital Dost.

Design goals:
  1. Every AI action (tool call, memory write, agent transition) produces a
     machine-readable JSON log line — queryable in Axiom / Datadog / Grafana Loki.
  2. In development mode, logs are pretty-printed with colour for readability.
  3. All log records carry a `session_id` and `user_id` for distributed tracing.
  4. Sensitive data (API keys, raw audio bytes) is never logged — scrubbed at source.

Usage:
    from app.utils.logger import get_logger
    log = get_logger(__name__)

    log.info("memory_retrieved", user_id="u123", chunks=5, latency_ms=42.3)
    log.agent_action("tool_called", tool="google_calendar", session_id="s456")
    log.error("pinecone_failed", error=str(e), user_id="u123")
"""

from __future__ import annotations

import logging
import sys
import time
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

# ── Context variables (set per-request via middleware) ─────────────────────────
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_user_id_var: ContextVar[str] = ContextVar("user_id", default="")
_session_id_var: ContextVar[str] = ContextVar("session_id", default="")


def set_request_context(
    request_id: str = "",
    user_id: str = "",
    session_id: str = "",
) -> None:
    """
    Call this from FastAPI middleware or at the start of a WebSocket handler
    to bind request-level context to all log records in this async task.
    """
    _request_id_var.set(request_id)
    _user_id_var.set(user_id)
    _session_id_var.set(session_id)


# ── Custom processors ──────────────────────────────────────────────────────────

def _inject_context_vars(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """
    Automatically injects the request-scoped context variables into every log record.
    This means you don't have to pass user_id/session_id to every log call.
    """
    if request_id := _request_id_var.get():
        event_dict.setdefault("request_id", request_id)
    if user_id := _user_id_var.get():
        event_dict.setdefault("user_id", user_id)
    if session_id := _session_id_var.get():
        event_dict.setdefault("session_id", session_id)
    return event_dict


def _scrub_sensitive_fields(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """
    Removes or masks sensitive data before any log record is emitted.
    Prevents API keys, raw audio, and passwords from appearing in log sinks.
    """
    SENSITIVE_KEYS = {
        "api_key", "password", "secret", "token", "authorization",
        "audio_data",       # Base64 audio chunks — too large and private
        "access_token", "refresh_token", "service_role_key",
    }
    for key in list(event_dict.keys()):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = "[REDACTED]"
        # Truncate very long string values (e.g. accidental content dumps)
        if isinstance(event_dict.get(key), str) and len(event_dict[key]) > 2000:
            event_dict[key] = event_dict[key][:2000] + "…[TRUNCATED]"
    return event_dict


def _add_service_metadata(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Stamps every log record with the service name and log level."""
    event_dict["service"] = "aayra-backend"
    event_dict["level"] = method_name.upper()
    return event_dict


# ── Renderer selection (JSON in prod, pretty in dev) ──────────────────────────

def _build_renderer(dev_mode: bool) -> Any:
    if dev_mode:
        return structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=structlog.dev.plain_traceback,
        )
    return structlog.processors.JSONRenderer()


# ── One-time configuration (call once at app startup) ─────────────────────────

def configure_logging(log_level: str = "INFO", dev_mode: bool = True) -> None:
    """
    Initialise structlog and the standard library logging bridge.
    Call this exactly ONCE from app/main.py on startup.

    Args:
        log_level: One of DEBUG / INFO / WARNING / ERROR / CRITICAL.
        dev_mode:  True → coloured console output.
                   False → newline-delimited JSON (production).
    """
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _inject_context_vars,
        _scrub_sensitive_fields,
        _add_service_metadata,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _build_renderer(dev_mode),
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "httpcore", "neo4j"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── AayraLogger: thin wrapper with domain-specific convenience methods ─────────

class AayraLogger:
    """
    Wraps a structlog BoundLogger with Aayra-specific semantic log methods.
    Provides type-safe, domain-aware logging for agent actions, memory ops, etc.

    Obtain an instance via get_logger(__name__), never instantiate directly.
    """

    def __init__(self, name: str) -> None:
        self._log = structlog.get_logger(name)

    # ── Standard levels (pass-through) ────────────────────────────────────────

    def debug(self, event: str, **kwargs: Any) -> None:
        self._log.debug(event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._log.info(event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._log.warning(event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._log.error(event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        self._log.critical(event, **kwargs)

    def exception(self, event: str, **kwargs: Any) -> None:
        """Log at ERROR level with full exception traceback attached."""
        self._log.exception(event, **kwargs)

    # ── Domain-specific semantic methods ──────────────────────────────────────

    def agent_action(
        self,
        event: str,
        *,
        agent_node: str,
        session_id: str = "",
        user_id: str = "",
        **kwargs: Any,
    ) -> None:
        """
        Log a LangGraph agent node transition or decision.
        Used by: agent_langgraph.py
        """
        self._log.info(
            event,
            log_type="agent_action",
            agent_node=agent_node,
            session_id=session_id or _session_id_var.get(),
            user_id=user_id or _user_id_var.get(),
            **kwargs,
        )

    def tool_call(
        self,
        tool_name: str,
        *,
        success: bool,
        duration_ms: float,
        session_id: str = "",
        user_id: str = "",
        error: str | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Audit log for every tool invocation. Captures name, outcome, latency.
        Used by: tool_registry.py, agent_langgraph.py
        """
        level = "info" if success else "error"
        getattr(self._log, level)(
            "tool_call_completed",
            log_type="tool_audit",
            tool_name=tool_name,
            success=success,
            duration_ms=round(duration_ms, 2),
            session_id=session_id or _session_id_var.get(),
            user_id=user_id or _user_id_var.get(),
            error=error,
            **kwargs,
        )

    def memory_op(
        self,
        operation: str,
        *,
        memory_type: str,
        user_id: str = "",
        chunks: int | None = None,
        latency_ms: float | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Log a memory read/write/consolidation operation.
        Used by: memory_pinecone.py, memory_neo4j.py, engine_consolidation.py
        """
        self._log.info(
            f"memory_{operation}",
            log_type="memory_op",
            memory_type=memory_type,
            user_id=user_id or _user_id_var.get(),
            chunks=chunks,
            latency_ms=round(latency_ms, 2) if latency_ms else None,
            **kwargs,
        )

    def ws_event(
        self,
        event: str,
        *,
        ws_event_type: str,
        session_id: str = "",
        user_id: str = "",
        **kwargs: Any,
    ) -> None:
        """
        Log a WebSocket frame sent or received.
        Used by: routes_voice.py
        """
        self._log.debug(
            event,
            log_type="ws_event",
            ws_event_type=ws_event_type,
            session_id=session_id or _session_id_var.get(),
            user_id=user_id or _user_id_var.get(),
            **kwargs,
        )

    def emotion_detected(
        self,
        *,
        primary_emotion: str,
        confidence: float,
        user_id: str = "",
        session_id: str = "",
    ) -> None:
        """Log an emotion detection result from Hume AI or text sentiment."""
        self._log.info(
            "emotion_detected",
            log_type="eq_event",
            primary_emotion=primary_emotion,
            confidence=round(confidence, 3),
            user_id=user_id or _user_id_var.get(),
            session_id=session_id or _session_id_var.get(),
        )

    def latency(
        self,
        operation: str,
        *,
        duration_ms: float,
        threshold_ms: float | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Log a latency measurement. Automatically warns if threshold exceeded.
        """
        exceeded = threshold_ms is not None and duration_ms > threshold_ms
        level = "warning" if exceeded else "info"
        getattr(self._log, level)(
            f"latency_{operation}",
            log_type="latency",
            duration_ms=round(duration_ms, 2),
            threshold_ms=threshold_ms,
            threshold_exceeded=exceeded,
            **kwargs,
        )


# ── Public factory ─────────────────────────────────────────────────────────────

def get_logger(name: str) -> AayraLogger:
    """
    Factory function. Import and call this in every module.

    Example:
        log = get_logger(__name__)
        log.info("server_started", port=8000)
        log.agent_action("planner_invoked", agent_node="planner", plan_steps=4)
    """
    return AayraLogger(name)


# ── Context manager for timing blocks ─────────────────────────────────────────

class LogTimer:
    """
    Context manager that measures elapsed time and emits a latency log.

    Usage:
        with LogTimer(log, "pinecone_query", threshold_ms=200, user_id=uid):
            results = await pinecone_client.query(...)
    """

    def __init__(
        self,
        logger: AayraLogger,
        operation: str,
        threshold_ms: float | None = None,
        **log_kwargs: Any,
    ) -> None:
        self._logger = logger
        self._operation = operation
        self._threshold_ms = threshold_ms
        self._log_kwargs = log_kwargs
        self._start: float = 0.0

    def __enter__(self) -> "LogTimer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        duration_ms = (time.perf_counter() - self._start) * 1000
        self._logger.latency(
            self._operation,
            duration_ms=duration_ms,
            threshold_ms=self._threshold_ms,
            **self._log_kwargs,
        )