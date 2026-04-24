"""
Logstash pipelines router — pipelines stored in Elasticsearch (_logstash/pipeline).

Endpoints:
  GET    /api/v1/logstash-pipelines          — list all pipelines
  GET    /api/v1/logstash-pipelines/{id}     — get one pipeline
  PUT    /api/v1/logstash-pipelines/{id}     — create / update pipeline
  DELETE /api/v1/logstash-pipelines/{id}     — delete pipeline

Query param ?target=nonprod|prod selects which ES cluster to hit.
"""
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from ..deps import ECEClient, get_ece_client

router = APIRouter(prefix="/api/v1/logstash-pipelines")


@router.get("")
def list_logstash_pipelines(
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """List all Logstash pipelines stored in the target ES cluster."""
    return client.list_logstash_pipelines(target)


@router.get("/{pipeline_id}")
def get_logstash_pipeline(
    pipeline_id: str,
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Get a single Logstash pipeline by ID."""
    return client.get_logstash_pipeline(pipeline_id, target)


@router.put("/{pipeline_id}", status_code=200)
def put_logstash_pipeline(
    pipeline_id: str,
    body: dict[str, Any] = Body(...),
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Create or update a Logstash pipeline."""
    return client.put_logstash_pipeline(pipeline_id, body, target)


@router.delete("/{pipeline_id}")
def delete_logstash_pipeline(
    pipeline_id: str,
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Delete a Logstash pipeline."""
    return client.delete_logstash_pipeline(pipeline_id, target)
