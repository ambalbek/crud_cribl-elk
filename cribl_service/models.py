"""
Pydantic v2 models for cribl_service.

Covers all resource types exposed by the service:
  - Routes (stream table entries)
  - Destinations (stream outputs)
  - Edge fleets
  - Worker groups
  - Pipelines
  - Provision (bulk route + destination push)
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── Routes ────────────────────────────────────────────────────────────────────


class RouteBase(BaseModel):
    """Shared fields for a Cribl route entry."""

    name: str = Field(..., description="Unique route name (used as the stable identifier)")
    filter: str = Field(..., description="Cribl filter expression, e.g. apmId == \"myapp\"")
    output: str = Field(..., description="Destination output ID that matching events are sent to")
    pipeline: str | None = Field(default=None, description="Pipeline to apply; falls back to service default if None")
    final: bool = Field(default=False, description="Stop evaluating further routes when True")
    disabled: bool = Field(default=False, description="Inactive route — still stored but not evaluated")
    description: str = Field(default="", description="Human-readable description of the route's purpose")
    clones: list[dict[str, Any]] = Field(default_factory=list, description="Clone configuration list (usually empty)")


class RouteCreate(RouteBase):
    """Request body for creating a new route (upserted above the catch-all)."""

    group_id: str = Field(default="", description="Insert into this route-group ID; empty = top-level routes")
    create_group: bool = Field(default=False, description="Create the group if it does not yet exist")
    group_name: str = Field(default="", description="Display name when creating a missing group")


class RouteUpdate(BaseModel):
    """Partial update fields for an existing route (all fields optional)."""

    model_config = ConfigDict(extra="allow")

    filter: str | None = Field(default=None, description="New filter expression")
    output: str | None = Field(default=None, description="New destination output ID")
    pipeline: str | None = Field(default=None, description="New pipeline assignment")
    final: bool | None = Field(default=None, description="Update final flag")
    disabled: bool | None = Field(default=None, description="Enable or disable the route")
    description: str | None = Field(default=None, description="Updated description")


class RouteResponse(BaseModel):
    """Response shape for a single route read from Cribl."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Route ID (usually matches name)")
    name: str = Field(..., description="Route name")
    filter: str = Field(default="", description="Filter expression")
    output: str = Field(default="", description="Output destination ID")
    pipeline: str | None = Field(default=None)
    final: bool = Field(default=False)
    disabled: bool = Field(default=False)
    description: str = Field(default="")


# ── Destinations (outputs) ────────────────────────────────────────────────────


class DestinationBase(BaseModel):
    """Shared fields for a Cribl output destination."""

    id: str = Field(..., description="Unique destination identifier")
    type: str = Field(default="azure_blob", description="Output type, e.g. azure_blob, s3, elastic")
    description: str = Field(default="", description="Human-readable destination description")


class DestinationCreate(DestinationBase):
    """Request body for creating a new destination."""

    model_config = ConfigDict(extra="allow")


class DestinationUpdate(BaseModel):
    """Partial update for an existing destination (all fields optional)."""

    model_config = ConfigDict(extra="allow")

    description: str | None = Field(default=None)
    type: str | None = Field(default=None)


class DestinationResponse(BaseModel):
    """Response shape for a single destination."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Destination ID")
    type: str = Field(default="", description="Output type")
    description: str = Field(default="")


# ── Edge Fleets ───────────────────────────────────────────────────────────────


class FleetBase(BaseModel):
    """Shared fields for a Cribl Edge fleet."""

    id: str = Field(..., description="Fleet identifier")
    name: str = Field(..., description="Human-readable fleet name")
    description: str = Field(default="", description="Fleet description")


class FleetCreate(FleetBase):
    """Request body for creating an edge fleet."""

    model_config = ConfigDict(extra="allow")


class FleetUpdate(BaseModel):
    """Partial update for an edge fleet."""

    model_config = ConfigDict(extra="allow")

    name: str | None = Field(default=None)
    description: str | None = Field(default=None)


class FleetResponse(BaseModel):
    """Response shape for a single fleet."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Fleet ID")
    name: str = Field(default="", description="Fleet name")
    description: str = Field(default="")


# ── Worker Groups ─────────────────────────────────────────────────────────────


class WorkGroupBase(BaseModel):
    """Shared fields for a Cribl worker group."""

    id: str = Field(..., description="Worker group identifier")
    name: str = Field(..., description="Human-readable name")
    description: str = Field(default="", description="Worker group description")


class WorkGroupCreate(WorkGroupBase):
    """Request body for creating a worker group."""

    model_config = ConfigDict(extra="allow")


class WorkGroupUpdate(BaseModel):
    """Partial update for a worker group."""

    model_config = ConfigDict(extra="allow")

    name: str | None = Field(default=None)
    description: str | None = Field(default=None)


class WorkGroupResponse(BaseModel):
    """Response shape for a single worker group."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Worker group ID")
    name: str = Field(default="")
    description: str = Field(default="")


# ── Pipelines ─────────────────────────────────────────────────────────────────


class PipelineBase(BaseModel):
    """Shared fields for a Cribl pipeline."""

    id: str = Field(..., description="Pipeline identifier")
    description: str = Field(default="", description="Pipeline description")
    functions: list[dict[str, Any]] = Field(default_factory=list, description="Ordered list of pipeline function configs")


class PipelineCreate(PipelineBase):
    """Request body for creating a pipeline."""

    model_config = ConfigDict(extra="allow")


class PipelineUpdate(BaseModel):
    """Partial update for a pipeline."""

    model_config = ConfigDict(extra="allow")

    description: str | None = Field(default=None)
    functions: list[dict[str, Any]] | None = Field(default=None)


class PipelineResponse(BaseModel):
    """Response shape for a single pipeline."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Pipeline ID")
    description: str = Field(default="")
    functions: list[dict[str, Any]] = Field(default_factory=list)


# ── Provision ─────────────────────────────────────────────────────────────────


class AppEntry(BaseModel):
    """Single app entry for bulk provisioning."""

    apmid: str = Field(..., description="APM ID / app identifier")
    app_name: str = Field(default="", description="Human-readable application name")


class ProvisionRequest(BaseModel):
    """Request body for POST /cribl/stream/provision."""

    apps: list[AppEntry] = Field(..., description="List of apps to provision routes and destinations for")
    route_template: dict[str, Any] = Field(..., description="Base route JSON template, copied and stamped per app")
    dest_template: dict[str, Any] = Field(..., description="Base destination JSON template, copied and stamped per app")
    dest_prefix: str = Field(..., description="Prefix for route name and destination ID, e.g. hcsc-blob-storage-azn")
    routes_table: str = Field(default="default", description="Cribl route table to modify")
    dry_run: bool = Field(default=True, description="When True, return a plan without making any writes")
    fallback_pipeline: str = Field(default="main", description="Pipeline assigned to routes whose template has none")


class ProvisionResult(BaseModel):
    """Response body for a provision operation."""

    worker_group: str = Field(..., description="Worker group that was provisioned")
    routes_table: str = Field(..., description="Route table that was modified")
    dry_run: bool = Field(..., description="Whether this was a dry run")
    new_routes: list[str] = Field(default_factory=list, description="Route names that were or would be added")
    new_destinations: list[str] = Field(default_factory=list, description="Destination IDs that were or would be added")
    skipped_routes: list[str] = Field(default_factory=list, description="App IDs whose routes already existed")
    skipped_dests: list[str] = Field(default_factory=list, description="Destination IDs that already existed")
