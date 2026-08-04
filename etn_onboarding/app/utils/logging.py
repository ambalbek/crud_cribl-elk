"""Structured JSON logging with OpenTelemetry trace-context injection.

Call ``setup_logging()`` once at application startup (after
``configure_otel``) to configure the root logger.  When the
``python-json-logger`` package is available, log records are emitted as
single-line JSON objects enriched with ``trace_id`` and ``span_id``
fields drawn from the current OTel span context.  If the package is
missing, a plain-text formatter is used as a fallback.
"""
from __future__ import annotations

import logging
import sys
from typing import Optional


def _current_trace_context() -> dict:
    """Return trace_id / span_id from the active OTel span, or empty strings."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            return {
                "trace_id": format(ctx.trace_id, "032x"),
                "span_id": format(ctx.span_id, "016x"),
            }
    except Exception:  # noqa: BLE001
        pass
    return {"trace_id": "", "span_id": ""}


class _OTelJsonFormatter(logging.Formatter):
    """JSON formatter that injects OTel trace context into every record.

    Requires ``python-json-logger`` (``pythonjsonlogger``).
    """

    def __init__(self) -> None:
        from pythonjsonlogger.json import JsonFormatter  # type: ignore[import-untyped]

        self._inner = JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        ctx = _current_trace_context()
        record.trace_id = ctx["trace_id"]  # type: ignore[attr-defined]
        record.span_id = ctx["span_id"]  # type: ignore[attr-defined]
        return self._inner.format(record)


def setup_logging(level: Optional[str] = "INFO") -> None:
    """Configure the root logger with structured JSON output.

    Parameters
    ----------
    level:
        Logging level name (default ``"INFO"``).  Accepts any value
        recognised by ``logging.getLevelName``.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, (level or "INFO").upper(), logging.INFO))

    # Remove any pre-existing handlers to avoid duplicate output.
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    try:
        formatter: logging.Formatter = _OTelJsonFormatter()
    except ImportError:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
        )

    handler.setFormatter(formatter)
    root.addHandler(handler)
