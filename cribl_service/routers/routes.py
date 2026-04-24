"""
Routes router — CRUD for Cribl Stream route tables.

Endpoints:
  GET    /api/v1/m/{worker_group}/routes/{table}              — fetch full table
  PATCH  /api/v1/m/{worker_group}/routes/{table}              — replace full table
  POST   /api/v1/m/{worker_group}/routes/{table}/route        — add one route (uses cribl_api insertion logic)
  DELETE /api/v1/m/{worker_group}/routes/{table}/route/{name} — remove route by name or id
"""
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from ..deps import CriblClient, get_cribl_client

router = APIRouter(prefix="/api/v1/m/{worker_group}/routes/{table}")


@router.get("")
def get_routes(
    worker_group: str,
    table: str,
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Fetch the full route table from Cribl."""
    return client.get_routes(worker_group, table)


@router.patch("")
def patch_routes(
    worker_group: str,
    table: str,
    payload: dict[str, Any] = Body(...),
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Replace the full route table (passthrough PATCH to Cribl)."""
    return client.patch_routes(worker_group, table, payload)


@router.post("/route")
def add_route(
    worker_group: str,
    table: str,
    route: dict[str, Any] = Body(...),
    group_id: str = Query(default="", description="Insert into this route-group ID"),
    create_group: bool = Query(default=False, description="Create the group if it does not exist"),
    group_name: str = Query(default="", description="Display name when creating a missing group"),
    fallback_pipeline: str = Query(default="main", description="Pipeline assigned if route has none"),
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """
    Insert a new route above the first final:true route in the table (or group).
    Uses cribl_api.normalize_route and find_default_route_index internally.
    """
    return client.add_route(
        worker_group, table, route,
        group_id=group_id, create_group=create_group,
        group_name=group_name, fallback_pipeline=fallback_pipeline,
    )


@router.delete("/route/{route_name}")
def delete_route(
    worker_group: str,
    table: str,
    route_name: str,
    group_id: str = Query(default="", description="Scope deletion to this route-group ID"),
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Remove a route by name or id, then PATCH the updated table back."""
    return client.delete_route(worker_group, table, route_name, group_id=group_id)
