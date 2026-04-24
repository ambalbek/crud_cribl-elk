"""
Leaders router — Cribl leader / system-level info endpoints.

Endpoints:
  GET   /api/v1/leaders/info          — system info (version, build, etc.)
  GET   /api/v1/leaders/settings      — git settings from the leader
  PATCH /api/v1/leaders/settings/git  — update git settings on the leader
"""
from typing import Any

from fastapi import APIRouter, Body, Depends

from ..deps import CriblClient, get_cribl_client

router = APIRouter(prefix="/api/v1/leaders")


@router.get("/info")
def get_system_info(
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Return Cribl system info (version, product, build) from /api/v1/system/info."""
    return client.get_system_info()


@router.get("/settings")
def get_git_settings(
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Return git settings from the Cribl leader node."""
    return client.get_git_settings()


@router.patch("/settings/git")
def update_git_settings(
    body: dict = Body(...),
    client: CriblClient = Depends(get_cribl_client),
) -> dict[str, Any]:
    """Update git settings on the Cribl leader node."""
    return client.update_git_settings(body)
