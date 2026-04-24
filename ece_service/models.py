"""
Pydantic v2 models for ece_service.

Covers ES security roles, role-mappings, index templates, ILM policies,
Logstash pipelines, Kibana dashboards, and app provisioning.
"""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── ES Security Roles ─────────────────────────────────────────────────────────

class RoleBase(BaseModel):
    cluster: list[str] = Field(default_factory=list, description="Cluster privileges")
    indices: list[dict[str, Any]] = Field(default_factory=list, description="Index privilege entries")
    applications: list[dict[str, Any]] = Field(default_factory=list, description="Application privileges")
    run_as: list[str] = Field(default_factory=list, description="Run-as privileges")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoleCreate(RoleBase):
    pass


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    cluster: list[str] | None = None
    indices: list[dict[str, Any]] | None = None
    applications: list[dict[str, Any]] | None = None
    run_as: list[str] | None = None
    metadata: dict[str, Any] | None = None


class RoleResponse(RoleBase):
    model_config = ConfigDict(extra="allow")


# ── ES Security Role-Mappings ─────────────────────────────────────────────────

class RoleMappingBase(BaseModel):
    enabled: bool = Field(default=True)
    roles: list[str] = Field(default_factory=list, description="Roles granted by this mapping")
    rules: dict[str, Any] = Field(default_factory=dict, description="Match rules (field/all/any)")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoleMappingCreate(RoleMappingBase):
    pass


class RoleMappingUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool | None = None
    roles: list[str] | None = None
    rules: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class RoleMappingResponse(RoleMappingBase):
    model_config = ConfigDict(extra="allow")


# ── ES Index / Index Template ─────────────────────────────────────────────────

class IndexTemplateBase(BaseModel):
    index_patterns: list[str] = Field(default_factory=list)
    template: dict[str, Any] = Field(default_factory=dict, description="Settings, mappings, aliases")
    priority: int = Field(default=100)
    composed_of: list[str] = Field(default_factory=list)
    _meta: dict[str, Any] = Field(default_factory=dict)


class IndexTemplateCreate(IndexTemplateBase):
    pass


class IndexTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    index_patterns: list[str] | None = None
    template: dict[str, Any] | None = None
    priority: int | None = None
    composed_of: list[str] | None = None


class IndexTemplateResponse(IndexTemplateBase):
    model_config = ConfigDict(extra="allow")


# ── ILM Policies ─────────────────────────────────────────────────────────────

class ILMPhaseAction(BaseModel):
    model_config = ConfigDict(extra="allow")


class ILMPhase(BaseModel):
    model_config = ConfigDict(extra="allow")

    min_age: str | None = Field(default=None, description="e.g. '30d'")
    actions: dict[str, Any] = Field(default_factory=dict)


class ILMPolicyBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    phases: dict[str, ILMPhase] = Field(
        default_factory=dict,
        description="hot/warm/cold/frozen/delete phase definitions",
    )


class ILMPolicyCreate(BaseModel):
    policy: ILMPolicyBody = Field(..., description="ILM policy definition")


class ILMPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    policy: ILMPolicyBody | None = None


class ILMPolicyResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    policy: dict[str, Any] = Field(default_factory=dict)


# ── Logstash Pipelines ────────────────────────────────────────────────────────

class LogstashPipelineBase(BaseModel):
    description: str = Field(default="")
    pipeline: str = Field(..., description="Logstash pipeline config string")
    pipeline_metadata: dict[str, Any] = Field(default_factory=dict)
    pipeline_settings: dict[str, Any] = Field(default_factory=dict)
    username: str = Field(default="")


class LogstashPipelineCreate(LogstashPipelineBase):
    pass


class LogstashPipelineUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    description: str | None = None
    pipeline: str | None = None
    pipeline_metadata: dict[str, Any] | None = None
    pipeline_settings: dict[str, Any] | None = None


class LogstashPipelineResponse(LogstashPipelineBase):
    model_config = ConfigDict(extra="allow")

    pipeline_id: str | None = None


# ── Kibana Dashboards ─────────────────────────────────────────────────────────

class KibanaDashboardBase(BaseModel):
    attributes: dict[str, Any] = Field(default_factory=dict, description="Dashboard attributes (title, panelsJSON, etc.)")
    references: list[dict[str, Any]] = Field(default_factory=list)


class KibanaDashboardCreate(KibanaDashboardBase):
    pass


class KibanaDashboardUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    attributes: dict[str, Any] | None = None
    references: list[dict[str, Any]] | None = None


class KibanaDashboardResponse(KibanaDashboardBase):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    type: str | None = None
    updated_at: str | None = None


# ── App Provisioning ──────────────────────────────────────────────────────────

class AppEntry(BaseModel):
    apmid: str = Field(..., description="APM application ID")
    app_name: str = Field(..., description="Human-readable application name")


class ProvisionConfig(BaseModel):
    region: str
    environment: str
    domain: str
    roles: list[str]


class ProvisionRequest(BaseModel):
    app_name: str = Field(..., description="Application name")
    apmid: str = Field(..., description="APM ID")
    configurations: list[ProvisionConfig] | None = Field(
        default=None, description="Override default onshore/offshore × test/prod matrix"
    )
    dry_run: bool = Field(default=False)


class ProvisionResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    app_name: str
    apmid: str
    dry_run: bool
    success: bool
    templates_saved_to: str | None = None
    error: str | None = None
