"""
Worker groups router — leader-level CRUD for Cribl worker groups.

Endpoints:
  GET    /api/v1/worker-groups           — list all worker groups
  GET    /api/v1/worker-groups/{id}      — get one worker group
  POST   /api/v1/worker-groups           — create a worker group
  PATCH  /api/v1/worker-groups/{id}      — update a worker group
  DELETE /api/v1/worker-groups/{id}      — delete a worker group
"""
from typing import Any

from fastapi import APIRouter, Body, Depends

from ..deps import CriblClient, get_cribl_client

router = APIRouter(prefix="/api/v1/worker-groups")


@router.get("")
def list_worker_groups(
    client: CriblClient = Depends(get_cribl_client),
) -> list[dict]:
    """List all worker groups from the Cribl leader."""
    return client.list_worker_groups()


@router.get("/{group_id}")
def get_worker_group(
    group_id: str,
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Get a single worker group by ID."""
    return client.get_worker_group(group_id)


@router.post("")
def create_worker_group(
    body: dict = Body(...),
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Create a new worker group."""
    return client.create_worker_group(body)


@router.patch("/{group_id}")
def update_worker_group(
    group_id: str,
    body: dict = Body(...),
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Update an existing worker group."""
    return client.update_worker_group(group_id, body)


@router.delete("/{group_id}")
def delete_worker_group(
    group_id: str,
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Delete a worker group by ID."""
    return client.delete_worker_group(group_id)
