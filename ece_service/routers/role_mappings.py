"""
Role-mappings router — ES security role-mappings CRUD.

Endpoints:
  GET    /api/v1/role-mappings          — list all role-mappings
  GET    /api/v1/role-mappings/{name}   — get one
  PUT    /api/v1/role-mappings/{name}   — create / update
  DELETE /api/v1/role-mappings/{name}   — delete

Query param ?target=nonprod|prod selects which ES cluster to hit.
"""
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from ..deps import ECEClient, get_ece_client

router = APIRouter(prefix="/api/v1/role-mappings")


@router.get("")
def list_role_mappings(
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """List all ES security role-mappings from the target cluster."""
    return client.list_role_mappings(target)


@router.get("/{name}")
def get_role_mapping(
    name: str,
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Get a single ES security role-mapping by name."""
    return client.get_role_mapping(name, target)


@router.put("/{name}", status_code=200)
def put_role_mapping(
    name: str,
    body: dict[str, Any] = Body(...),
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Create or update an ES security role-mapping."""
    return client.put_role_mapping(name, body, target)


@router.delete("/{name}")
def delete_role_mapping(
    name: str,
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Delete an ES security role-mapping."""
    return client.delete_role_mapping(name, target)
