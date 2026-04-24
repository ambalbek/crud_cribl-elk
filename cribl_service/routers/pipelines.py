"""
Pipelines router — CRUD for Cribl Stream pipelines.

Endpoints:
  GET    /api/v1/m/{worker_group}/pipelines          — list all pipelines
  GET    /api/v1/m/{worker_group}/pipelines/{id}     — get one pipeline
  POST   /api/v1/m/{worker_group}/pipelines          — create pipeline
  PATCH  /api/v1/m/{worker_group}/pipelines/{id}     — update pipeline
  DELETE /api/v1/m/{worker_group}/pipelines/{id}     — delete pipeline
"""
from typing import Any

from fastapi import APIRouter, Body, Depends

from ..deps import CriblClient, get_cribl_client

router = APIRouter(prefix="/api/v1/m/{worker_group}/pipelines")


@router.get("")
def list_pipelines(
    worker_group: str,
    client: CriblClient = Depends(get_cribl_client),
) -> list[dict]:
    """List all pipelines for the worker group."""
    return client.list_pipelines(worker_group)


@router.get("/{pipeline_id}")
def get_pipeline(
    worker_group: str,
    pipeline_id: str,
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Fetch a single pipeline by ID."""
    return client.get_pipeline(worker_group, pipeline_id)


@router.post("", status_code=201)
def create_pipeline(
    worker_group: str,
    payload: dict[str, Any] = Body(...),
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Create a new pipeline."""
    return client.create_pipeline(worker_group, payload)


@router.patch("/{pipeline_id}")
def update_pipeline(
    worker_group: str,
    pipeline_id: str,
    payload: dict[str, Any] = Body(...),
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Update an existing pipeline."""
    return client.update_pipeline(worker_group, pipeline_id, payload)


@router.delete("/{pipeline_id}")
def delete_pipeline(
    worker_group: str,
    pipeline_id: str,
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Delete a pipeline by ID."""
    return client.delete_pipeline(worker_group, pipeline_id)
