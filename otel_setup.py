"""
otel_setup.py — Shared OpenTelemetry bootstrap for all three services.

Environment variables:
  OTEL_SERVICE_NAME             Override the service_name passed to configure_otel()
  OTEL_EXPORTER_OTLP_ENDPOINT   e.g. http://otel-collector:4318
                                 If empty, spans are collected but not exported.
  OTEL_TRACES_EXPORTER          Set to "console" to print spans to stdout (dev/debug).
  LOG_FORMAT                    Set to "json" for structured JSON log output.
"""
import logging
import os
import sys

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter


def configure_otel(service_name: str) -> None:
    """
    Initialize the OTel TracerProvider and auto-instrument outbound HTTP.
    Safe to call multiple times — subsequent calls are no-ops if a real
    provider is already set.
    """
    if not isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        # Already initialised (e.g. called twice during hot-reload)
        return

    svc = os.environ.get("OTEL_SERVICE_NAME", service_name)
    resource = Resource.create({SERVICE_NAME: svc})
    provider = TracerProvider(resource=resource)

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
    exporter_env = os.environ.get("OTEL_TRACES_EXPORTER", "").lower()

    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logging.getLogger("otel_setup").info(
                "OTel traces → OTLP %s  service=%s", endpoint, svc
            )
        except ImportError:
            logging.getLogger("otel_setup").warning(
                "opentelemetry-exporter-otlp-proto-http not installed; traces disabled"
            )
    elif exporter_env == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logging.getLogger("otel_setup").info(
            "OTel traces → console (stdout)  service=%s", svc
        )
    # else: no exporter — context propagation still works, spans just aren't exported

    trace.set_tracer_provider(provider)

    # Auto-instrument outbound HTTP so downstream calls inherit trace context
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except ImportError:
        pass


def make_json_formatter(service_label: str = "") -> logging.Formatter:
    """
    Return a JSON log formatter that injects OTel trace_id / span_id fields.
    Falls back to a plain text formatter if python-json-logger isn't installed.
    """
    try:
        from pythonjsonlogger import jsonlogger

        _label = service_label

        class _TraceJsonFormatter(jsonlogger.JsonFormatter):
            def add_fields(self, log_record, record, message_dict):
                super().add_fields(log_record, record, message_dict)
                if _label:
                    log_record.setdefault("service", _label)
                span = trace.get_current_span()
                ctx = span.get_span_context()
                if ctx.is_valid:
                    log_record["trace_id"] = format(ctx.trace_id, "032x")
                    log_record["span_id"] = format(ctx.span_id, "016x")

        return _TraceJsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    except ImportError:
        return logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )


def use_json_logging() -> bool:
    """True when LOG_FORMAT=json is set in the environment."""
    return os.environ.get("LOG_FORMAT", "").lower() == "json"
