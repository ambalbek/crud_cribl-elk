"""
Destinations router — CRUD for Cribl Stream outputs (destinations).

Endpoints:
  GET    /api/v1/m/{worker_group}/destinations          — list all outputs
  GET    /api/v1/m/{worker_group}/destinations/{id}     — get one output
  POST   /api/v1/m/{worker_group}/destinations          — create output
  PATCH  /api/v1/m/{worker_group}/destinations/{id}     — update output
  DELETE /api/v1/m/{worker_group}/destinations/{id}     — delete output
"""
from typing import Any

from fastapi import APIRouter, Body, Depends

from ..deps import CriblClient, get_cribl_client

router = APIRouter(prefix="/api/v1/m/{worker_group}/destinations")


@router.get("")
def list_outputs(
    worker_group: str,
    client: CriblClient = Depends(get_cribl_client),
) -> list[dict]:
    """List all output destinations for the worker group."""
    return client.list_outputs(worker_group)


@router.get("/{output_id}")
def get_output(
    worker_group: str,
    output_id: str,
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Fetch a single output destination by ID."""
    return client.get_output(worker_group, output_id)


@router.post("", status_code=201)
def create_output(
    worker_group: str,
    payload: dict[str, Any] = Body(...),
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Create a new output destination (e.g. Azure Blob Storage)."""
    return client.create_output(worker_group, payload)


@router.patch("/{output_id}")
def update_output(
    worker_group: str,
    output_id: str,
    payload: dict[str, Any] = Body(...),
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Update an existing output destination."""
    return client.update_output(worker_group, output_id, payload)


@router.delete("/{output_id}")
def delete_output(
    worker_group: str,
    output_id: str,
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Delete an output destination by ID."""
    return client.delete_output(worker_group, output_id)
