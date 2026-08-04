import logging
import sys

from flask import Flask
from pythonjsonlogger import jsonlogger

from app.extensions import db, migrate
from app.routes import register_blueprints
from app.services import init_services
from config import Config


def _configure_logging(flask_app: Flask) -> None:
    """Set up structured JSON logging for the application."""
    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    flask_app.logger.handlers = root.handlers
    flask_app.logger.setLevel(logging.INFO)


def _configure_otel(flask_app: Flask) -> None:
    """Bootstrap OpenTelemetry tracing if an OTLP endpoint is configured."""
    endpoint = flask_app.config.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        flask_app.logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set; skipping OTel init")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.flask import FlaskInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        resource = Resource.create(
            {"service.name": flask_app.config.get("OTEL_SERVICE_NAME", "etn-onboarding")}
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )
        trace.set_tracer_provider(provider)

        FlaskInstrumentor().instrument_app(flask_app)
        RequestsInstrumentor().instrument()

        flask_app.logger.info("OpenTelemetry tracing initialized (endpoint=%s)", endpoint)
    except ImportError:
        flask_app.logger.warning(
            "OpenTelemetry packages not installed; tracing disabled"
        )


def create_app(config_class=None) -> Flask:
    """Application factory for the ETN Onboarding service."""
    flask_app = Flask(__name__)
    flask_app.config.from_object(config_class or Config)

    # Extensions
    db.init_app(flask_app)
    migrate.init_app(flask_app, db)

    # Import models so Alembic can detect them
    with flask_app.app_context():
        from app import models  # noqa: F401

    # Blueprints
    register_blueprints(flask_app)

    # Service clients
    init_services(flask_app)

    # Observability
    _configure_logging(flask_app)
    _configure_otel(flask_app)

    return flask_app
