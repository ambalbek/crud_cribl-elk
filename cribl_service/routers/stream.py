"""
Stream router — semantic CRUD for Cribl Stream routes and destinations.

Wraps the Cribl API routes/outputs with cleaner REST semantics and
full CRUD (including upsert-with-idempotency for routes).

All endpoints use the async httpx-based AsyncCriblClient.

Route endpoints — prefix /cribl/stream/routes:
  GET    /                      — list full route table
  GET    /{app_id}              — get single route matching app_id
  POST   /                      — upsert route above catch-all (idempotent)
  PATCH  /{app_id}              — update route fields
  DELETE /{app_id}              — remove route from table

Destination endpoints — prefix /cribl/stream/destinations:
  GET    /                      — list all destinations
  GET    /{dest_id}             — get single destination
  POST   /                      — create destination
  PATCH  /{dest_id}             — update destination
  DELETE /{dest_id}             — delete destination
"""
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ..cribl_client import AsyncCriblClient, get_async_cribl_client
from ..models import (
    DestinationCreate,
    DestinationResponse,
    DestinationUpdate,
    RouteCreate,
    RouteResponse,
    RouteUpdate,
)

router = APIRouter()

# ── Shared query params ────────────────────────────────────────────────────────

_WG_Q  = Query(...,          description="Cribl worker group name")
_TBL_Q = Query("default",   description="Route table name (default: 'default')")
_DRY_Q = Query(False,       description="Dry run — preview changes without writing")


# ══════════════════════════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/cribl/stream/routes")
async def list_routes(
    worker_group: str = _WG_Q,
    routes_table: str = _TBL_Q,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Return the full route table for the given worker group and table."""
    return await client.list_routes(worker_group, routes_table)


@router.get("/cribl/stream/routes/{app_id}")
async def get_route(
    app_id: str,
    worker_group: str = _WG_Q,
    routes_table: str = _TBL_Q,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """
    Return the route whose id or name matches app_id.
    Returns 404 if not found.
    """
    route = await client.get_route_by_app(worker_group, routes_table, app_id)
    if route is None:
        raise HTTPException(status_code=404, detail=f"Route '{app_id}' not found in table '{routes_table}'")
    return route


@router.post("/cribl/stream/routes", status_code=201)
async def create_route(
    body: RouteCreate,
    worker_group: str = _WG_Q,
    routes_table: str = _TBL_Q,
    dry_run: bool = _DRY_Q,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """
    Upsert a route above the first final:true (catch-all) route.

    Idempotent — if a route with the same name or filter already exists,
    returns status='skipped' without modifying the table.

    Saves a rollback snapshot to CRIBL_SNAPSHOT_DIR before patching (unless dry_run).
    """
    return await client.upsert_route(
        worker_group=worker_group,
        table=routes_table,
        route=body.model_dump(exclude={"group_id", "create_group", "group_name"}),
        group_id=body.group_id,
        create_group=body.create_group,
        group_name=body.group_name,
        dry_run=dry_run,
    )


@router.patch("/cribl/stream/routes/{app_id}")
async def update_route(
    app_id: str,
    body: RouteUpdate,
    worker_group: str = _WG_Q,
    routes_table: str = _TBL_Q,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """
    Update fields of an existing route by app_id (matches on id or name).
    PATCH the full table back to Cribl after mutating the target route.
    """
    return await client.update_route(
        worker_group, routes_table, app_id,
        body.model_dump(exclude_none=True),
    )


@router.delete("/cribl/stream/routes/{app_id}")
async def delete_route(
    app_id: str,
    worker_group: str = _WG_Q,
    routes_table: str = _TBL_Q,
    dry_run: bool = _DRY_Q,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """
    Remove the route matching app_id, then PATCH the updated table back.
    Saves a rollback snapshot before patching (unless dry_run).
    """
    return await client.delete_route(worker_group, routes_table, app_id, dry_run=dry_run)


# ══════════════════════════════════════════════════════════════════════════════
# Destinations
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/cribl/stream/destinations")
async def list_destinations(
    worker_group: str = _WG_Q,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> list[dict]:
    """List all output destinations for the worker group."""
    return await client.list_destinations(worker_group)


@router.get("/cribl/stream/destinations/{dest_id}")
async def get_destination(
    dest_id: str,
    worker_group: str = _WG_Q,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Get a single destination by ID."""
    return await client.get_destination(worker_group, dest_id)


@router.post("/cribl/stream/destinations", status_code=201)
async def create_destination(
    body: DestinationCreate,
    worker_group: str = _WG_Q,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Create a new output destination (e.g. Azure Blob Storage)."""
    return await client.create_destination(worker_group, body.model_dump())


@router.patch("/cribl/stream/destinations/{dest_id}")
async def update_destination(
    dest_id: str,
    body: DestinationUpdate,
    worker_group: str = _WG_Q,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Update an existing destination."""
    return await client.update_destination(worker_group, dest_id, body.model_dump(exclude_none=True))


@router.delete("/cribl/stream/destinations/{dest_id}")
async def delete_destination(
    dest_id: str,
    worker_group: str = _WG_Q,
    client: AsyncCriblClient = Depends(get_async_cribl_client),
) -> dict[str, Any]:
    """Delete a destination by ID."""
    return await client.delete_destination(worker_group, dest_id)
