"""
ILM policies router — CRUD for Elasticsearch ILM lifecycle policies.

Endpoints:
  GET    /ece/indexes/ilm/policies                — list all ILM policies
  GET    /ece/indexes/ilm/policies/{name}         — get one policy
  PUT    /ece/indexes/ilm/policies/{name}         — create or replace a policy
  DELETE /ece/indexes/ilm/policies/{name}         — delete a policy
  GET    /ece/indexes/ilm/explain/{index}         — explain ILM status of an index

Query params:
  target=prod   — use the prod ES cluster (default: nonprod)
"""
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from ..ece_client import AsyncECEClient, get_async_ece_client

router = APIRouter(prefix="/ece/indexes/ilm")


@router.get("/policies")
async def list_ilm_policies(
    target: str = Query(default="nonprod", description="'nonprod' or 'prod'"),
    client: AsyncECEClient = Depends(get_async_ece_client),
) -> dict[str, Any]:
    """List all ILM policies from the target ES cluster."""
    return await client.list_ilm_policies(prod=(target == "prod"))


@router.get("/policies/{name}")
async def get_ilm_policy(
    name: str,
    target: str = Query(default="nonprod"),
    client: AsyncECEClient = Depends(get_async_ece_client),
) -> dict[str, Any]:
    """Fetch a single ILM policy by name."""
    return await client.get_ilm_policy(name, prod=(target == "prod"))


@router.put("/policies/{name}", status_code=200)
async def put_ilm_policy(
    name: str,
    body: dict[str, Any] = Body(...),
    target: str = Query(default="nonprod"),
    client: AsyncECEClient = Depends(get_async_ece_client),
) -> dict[str, Any]:
    """Create or replace an ILM policy."""
    return await client.put_ilm_policy(name, body, prod=(target == "prod"))


@router.delete("/policies/{name}")
async def delete_ilm_policy(
    name: str,
    target: str = Query(default="nonprod"),
    client: AsyncECEClient = Depends(get_async_ece_client),
) -> dict[str, Any]:
    """Delete an ILM policy by name."""
    return await client.delete_ilm_policy(name, prod=(target == "prod"))


@router.get("/explain/{index}")
async def explain_ilm(
    index: str,
    target: str = Query(default="nonprod"),
    client: AsyncECEClient = Depends(get_async_ece_client),
) -> dict[str, Any]:
    """Return ILM explain output for an index (shows current phase, age, actions)."""
    return await client.explain_ilm(index, prod=(target == "prod"))
