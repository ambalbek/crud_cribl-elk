"""
cribl_service — FastAPI microservice wrapping the Cribl Stream API.

Routers:
  /api/v1/m/{worker_group}/routes/*        — Routes CRUD
  /api/v1/m/{worker_group}/destinations/*  — Destinations (outputs) CRUD
  /api/v1/m/{worker_group}/pipelines/*     — Pipelines CRUD
  /api/v1/worker-groups                    — Worker group listing (leader-level)
  /api/v1/leaders/*                        — Leader / system info

Run:
    uvicorn cribl_service.main:app --host 0.0.0.0 --port 8000
"""
import logging
import os
import sys

# Ensure project root is importable before any shared-module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from otel_setup import configure_otel, make_json_formatter, use_json_logging

configure_otel("cribl-service")

from fastapi import FastAPI

from .routers import destinations, edge, leaders, pipelines, provision, routes, stream, worker_groups, workgroups
from .settings import LOG_LEVEL

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(
    make_json_formatter("cribl-service") if use_json_logging()
    else logging.Formatter(
        "%(asctime)s  %(levelname)-8s  [cribl-service]  %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    handlers=[_handler],
    force=True,
)

app = FastAPI(
    title="Cribl Service",
    description="CRUD endpoints for Cribl Stream: routes, destinations, pipelines, worker groups, leaders.",
    version="1.0.0",
)

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(app)
except ImportError:
    pass

app.include_router(routes.router,        tags=["routes"])
app.include_router(provision.router,     tags=["provision"])
app.include_router(destinations.router,  tags=["destinations"])
app.include_router(pipelines.router,     tags=["pipelines"])
app.include_router(worker_groups.router, tags=["worker-groups"])
app.include_router(leaders.router,       tags=["leaders"])
app.include_router(stream.router,        tags=["stream"])
app.include_router(edge.router,          tags=["edge"])
app.include_router(workgroups.router,    tags=["workgroups"])


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "service": "cribl_service"}
