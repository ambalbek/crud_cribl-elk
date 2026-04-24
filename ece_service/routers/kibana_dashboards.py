"""
Kibana dashboards router — saved objects API for dashboards.
Requires ECE_KIBANA_URL to be set.

Endpoints:
  GET    /api/v1/kibana/dashboards          — list dashboards (saved objects _find)
  GET    /api/v1/kibana/dashboards/{id}     — get one dashboard
  POST   /api/v1/kibana/dashboards          — create dashboard
  PUT    /api/v1/kibana/dashboards/{id}     — update dashboard
  DELETE /api/v1/kibana/dashboards/{id}     — delete dashboard
"""
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from ..deps import ECEClient, get_ece_client

router = APIRouter(prefix="/api/v1/kibana/dashboards")


@router.get("")
def list_dashboards(
    search: str   = Query(default="", description="Title search string"),
    page: int     = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=1000),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """List Kibana dashboards via the saved objects _find API."""
    return client.list_dashboards(search, page, per_page)


@router.get("/{dashboard_id}")
def get_dashboard(
    dashboard_id: str,
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Get a single Kibana dashboard by ID."""
    return client.get_dashboard(dashboard_id)


@router.post("", status_code=200)
def create_dashboard(
    body: dict[str, Any] = Body(...),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Create a Kibana dashboard (saved objects API)."""
    return client.create_dashboard(body)


@router.put("/{dashboard_id}")
def update_dashboard(
    dashboard_id: str,
    body: dict[str, Any] = Body(...),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Update an existing Kibana dashboard."""
    return client.update_dashboard(dashboard_id, body)


@router.delete("/{dashboard_id}")
def delete_dashboard(
    dashboard_id: str,
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Delete a Kibana dashboard."""
    return client.delete_dashboard(dashboard_id)
