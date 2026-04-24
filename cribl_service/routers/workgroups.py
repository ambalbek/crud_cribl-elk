"""
Workgroups router — full CRUD for Cribl worker groups at leader level.

Wraps the Cribl /api/v1/master/groups API endpoints.
All endpoints use the async httpx-based AsyncCriblClient.

Prefix: /cribl/workgroups
  GET    /          — list all worker groups
  GET    /{id}      — get single worker group
  POST   /          — create worker group
  PATCH  /{id}      — update worker group
  DELETE /{id}      — delete worker group
"""
from typing import Any

from fastapi import APIRouter, Body, Depends

from ..cribl_client import AsyncCriblClient, get_async_cribl_client
from ..models import WorkGroupCreate, WorkGroupResponse, WorkGroupUpdate

router = APIRouter(prefix="/cribl/workgroups")


@router.get("")
async def list_workgroups(
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> list[dict]:
    """List all worker groups from the Cribl leader (/api/v1/master/groups)."""
    return await client.list_workgroups()


@router.get("/{wg_id}")
async def get_workgroup(
    wg_id: str,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Get a single worker group by ID."""
    return await client.get_workgroup(wg_id)


@router.post("", status_code=201)
async def create_workgroup(
    body: WorkGroupCreate,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Create a new worker group on the Cribl leader."""
    return await client.create_workgroup(body.model_dump())


@router.patch("/{wg_id}")
async def update_workgroup(
    wg_id: str,
    body: WorkGroupUpdate,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Update an existing worker group."""
    return await client.update_workgroup(wg_id, body.model_dump(exclude_none=True))


@router.delete("/{wg_id}")
async def delete_workgroup(
    wg_id: str,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Delete a worker group by ID."""
    return await client.delete_workgroup(wg_id)
