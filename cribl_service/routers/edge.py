"""
Edge router — CRUD for Cribl Edge fleets.

Wraps the Cribl /api/v1/fleet API endpoints.
All endpoints use the async httpx-based AsyncCriblClient.

Prefix: /cribl/edge/fleets
  GET    /          — list all fleets
  GET    /{id}      — get single fleet
  POST   /          — create fleet
  PATCH  /{id}      — update fleet
  DELETE /{id}      — delete fleet
"""
from typing import Any

from fastapi import APIRouter, Body, Depends

from ..cribl_client import AsyncCriblClient, get_async_cribl_client
from ..models import FleetCreate, FleetResponse, FleetUpdate

router = APIRouter(prefix="/cribl/edge/fleets")


@router.get("")
async def list_fleets(
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> list[dict]:
    """List all Cribl Edge fleets from /api/v1/fleet."""
    return await client.list_fleets()


@router.get("/{fleet_id}")
async def get_fleet(
    fleet_id: str,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Get a single Cribl Edge fleet by ID."""
    return await client.get_fleet(fleet_id)


@router.post("", status_code=201)
async def create_fleet(
    body: FleetCreate,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Create a new Cribl Edge fleet."""
    return await client.create_fleet(body.model_dump())


@router.patch("/{fleet_id}")
async def update_fleet(
    fleet_id: str,
    body: FleetUpdate,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Update an existing Cribl Edge fleet."""
    return await client.update_fleet(fleet_id, body.model_dump(exclude_none=True))


@router.delete("/{fleet_id}")
async def delete_fleet(
    fleet_id: str,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Delete a Cribl Edge fleet by ID."""
    return await client.delete_fleet(fleet_id)
