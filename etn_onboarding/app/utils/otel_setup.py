"""OpenTelemetry bootstrap for ETN Onboarding service."""
import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter


def configure_otel(service_name: str = "etn-onboarding") -> None:
    """Initialise OpenTelemetry tracing for the Flask application.

    Safe to call multiple times — returns immediately if a provider is
    already configured.

    Environment variables
    ---------------------
    OTEL_SERVICE_NAME
        Override the default service name.
    OTEL_EXPORTER_OTLP_ENDPOINT
        If set, traces are exported via OTLP/HTTP to this endpoint.
    OTEL_TRACES_EXPORTER
        Set to ``console`` to emit spans to stdout (useful for local dev).
    """
    # Avoid double-initialisation.
    if not isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        return

    svc = os.environ.get("OTEL_SERVICE_NAME", service_name)
    resource = Resource.create({SERVICE_NAME: svc})
    provider = TracerProvider(resource=resource)

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except ImportError:
            logging.getLogger("otel_setup").warning(
                "OTLP exporter not installed"
            )
    elif os.environ.get("OTEL_TRACES_EXPORTER", "").lower() == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    # Auto-instrument outbound HTTP calls made via ``requests``.
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()
    except ImportError:
        pass

    # Auto-instrument inbound Flask requests.
    try:
        from opentelemetry.instrumentation.flask import FlaskInstrumentor

        FlaskInstrumentor().instrument()
    except ImportError:
        pass
