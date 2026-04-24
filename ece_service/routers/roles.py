"""
Roles router — ES security roles + app provisioning.

Endpoints:
  GET    /api/v1/roles                      — list all roles
  GET    /api/v1/roles/{name}               — get one role
  PUT    /api/v1/roles/{name}               — create / update role
  DELETE /api/v1/roles/{name}               — delete role
  POST   /api/v1/roles/generate             — generate templates only (no push)
  POST   /api/v1/roles/provision            — generate + push roles AND role-mappings for an app

Query param ?target=nonprod|prod selects which ES cluster to hit.
"""
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from ..deps import ECEClient, get_ece_client

router = APIRouter(prefix="/api/v1/roles")


@router.get("")
def list_roles(
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """List all ES security roles from the target cluster."""
    return client.list_roles(target)


@router.get("/{name}")
def get_role(
    name: str,
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Get a single ES security role by name."""
    return client.get_role(name, target)


@router.put("/{name}", status_code=200)
def put_role(
    name: str,
    body: dict[str, Any] = Body(...),
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Create or update an ES security role."""
    return client.put_role(name, body, target)


@router.delete("/{name}")
def delete_role(
    name: str,
    target: str = Query(default="nonprod", pattern="^(nonprod|prod)$"),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """Delete an ES security role."""
    return client.delete_role(name, target)


@router.post("/generate")
def generate_templates(
    app_name: str = Query(..., description="Application name (e.g. APP00000001-MY-APP)"),
    apmid: str    = Query(..., description="APM ID / lower-cased org name"),
    save: bool    = Query(default=True, description="Save generated files to ops_rm_r_templates_output/"),
    configurations: list[dict[str, Any]] | None = Body(default=None,
        description="Override default 4-config list (onshore/offshore × test/prod). "
                    "Omit to use defaults from role_rm.py."),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """
    Generate ELK role + role-mapping templates using role_rm.generate_templates().
    No API calls are made — returns rendered template strings and optionally
    saves them to ops_rm_r_templates_output/.
    """
    return client.generate_app_templates(app_name, apmid, configurations, save_to_disk=save)


@router.post("/provision")
def provision_app(
    app_name: str  = Query(..., description="Application name"),
    apmid: str     = Query(..., description="APM ID"),
    dry_run: bool  = Query(default=True, description="Preview only — no API writes"),
    configurations: list[dict[str, Any]] | None = Body(default=None,
        description="Override default configurations. Omit to use defaults."),
    client: ECEClient = Depends(get_ece_client),
) -> dict[str, Any]:
    """
    Full app provisioning: generates templates, saves them to disk,
    then PUTs roles and role-mappings to both nonprod and prod ES clusters.
    Wraps role_rm.push_elk() — ECE_ES_URL and ECE_ES_URL_PROD must be set.
    Defaults to dry_run=True.
    """
    return client.provision_app(app_name, apmid, configurations, dry_run)
