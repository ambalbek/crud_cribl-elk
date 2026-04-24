"""
Indexes router — Elasticsearch index management.

Endpoints:
  GET    /api/v1/indexes                        — list indexes (_cat/indices)
  GET    /api/v1/indexes/{name}                 — get index settings + mappings
  PUT    /api/v1/indexes/{name}                 — create / update index
  DELETE /api/v1/indexes/{name}                 — delete index
  PUT    /api/v1/indexes/templates/{name}       — create / update index template

Query param ?target=nonprod|prod selects which ES cluster to hit.
"""
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from ..deps import ECEClient, get_ece_client

router = APIRouter(prefix="/api/v1/indexes")


@router.get("")
def list_indexes(
    pattern: str = Query(default="*", description="Index name pattern (supports wildcards)"),
    target: str  = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> list:
    """List indexes matching the pattern via _cat/indices."""
    return client.list_indexes(pattern, target)


@router.get("/templates/{name}")
def get_index_template(
    name: str,
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Get an index template by name."""
    return client.get_index_template(name, target)


@router.put("/templates/{name}", status_code=200)
def put_index_template(
    name: str,
    body: dict[str, Any] = Body(...),
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Create or update an index template."""
    return client.put_index_template(name, body, target)


@router.get("/{name}")
def get_index(
    name: str,
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Get index settings and mappings."""
    return client.get_index(name, target)


@router.put("/{name}", status_code=200)
def put_index(
    name: str,
    body: dict[str, Any] = Body(default={}),
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Create or update an index (pass settings/mappings in body)."""
    return client.put_index(name, body, target)


@router.delete("/{name}")
def delete_index(
    name: str,
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Delete an index."""
    return client.delete_index(name, target)
