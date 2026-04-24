"""
ece_service — FastAPI microservice wrapping Elasticsearch and Kibana APIs.

Routers:
  /api/v1/roles/*                  — ES security roles (wraps role_rm.py templates + push)
  /api/v1/role-mappings/*          — ES security role-mappings
  /api/v1/indexes/*                — ES index management
  /api/v1/logstash-pipelines/*     — Logstash pipelines stored in ES
  /api/v1/kibana/dashboards/*      — Kibana saved-objects dashboards

Run:
    uvicorn ece_service.main:app --host 0.0.0.0 --port 8001
"""
import logging
import os
import sys

# Ensure project root is importable before any shared-module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from otel_setup import configure_otel, make_json_formatter, use_json_logging

configure_otel("ece-service")

from fastapi import FastAPI

from .routers import ilm, indexes, kibana_dashboards, logstash_pipelines, role_mappings, roles
from .settings import LOG_LEVEL

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(
    make_json_formatter("ece-service") if use_json_logging()
    else logging.Formatter(
        "%(asctime)s  %(levelname)-8s  [ece-service]  %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    handlers=[_handler],
    force=True,
)

app = FastAPI(
    title="ECE Service",
    description=(
        "CRUD endpoints for Elastic Cloud Enterprise: "
        "ES security roles, role-mappings, indexes, "
        "Logstash pipelines, and Kibana dashboards."
    ),
    version="1.0.0",
)

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(app)
except ImportError:
    pass

app.include_router(roles.router,               tags=["roles"])
app.include_router(role_mappings.router,       tags=["role-mappings"])
app.include_router(indexes.router,             tags=["indexes"])
app.include_router(ilm.router,                 tags=["ilm"])
app.include_router(logstash_pipelines.router,  tags=["logstash-pipelines"])
app.include_router(kibana_dashboards.router,   tags=["kibana-dashboards"])


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "service": "ece_service"}
