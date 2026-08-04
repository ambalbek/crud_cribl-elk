# ETN Onboarding Service -- Architecture

## System Overview

```
                          +------------------+
                          |   Flask App      |
                          |  (etn_onboarding)|
                          +--------+---------+
                                   |
              +--------------------+--------------------+
              |                    |                     |
     +--------v-------+  +--------v--------+  +---------v--------+
     |  PostgreSQL     |  | Service Clients |  | OpenTelemetry    |
     |  (SQLAlchemy)   |  | (stubs)         |  | Collector        |
     +--------+--------+  +--------+--------+  +------------------+
              |                     |
              |          +----------+----------+----------+
              |          |          |          |          |
              |    +-----v--+ +----v---+ +----v----+ +---v------+
              |    | Cribl  | |  ECE   | |  ETN    | | Harness  |
              |    | Stream | | (ELK)  | | Portal  | |          |
              |    +--------+ +--------+ +---------+ +----------+
              |
     +--------v--------+
     | Tables           |
     |  onboarding_     |
     |   requests       |
     |  audit_logs      |
     |  delivery_jobs   |
     +-----------------+
```

The Flask application is the single entry point. It owns all business logic, persists state in PostgreSQL, and delegates external provisioning to pluggable service clients.

## State Machine

### Status Lifecycle

Every `OnboardingRequest` follows a strict linear progression through five high-level stages. The Delivery stage is further decomposed into four sub-stages that map to discrete provisioning actions.

```
                     +----------------+
                     | intake_pending |  (initial)
                     +-------+--------+
                             |
                     +-------v-----------+
                     | intake_validated   |
                     +-------+-----------+
                             |
                     +-------v--------+
                     |  engagement    |
                     +-------+--------+
                             |
                     +-------v--------+
                     |  solutioning   |
                     +-------+--------+
                             |
                +------------v--------------+
                | delivery_collection       |
                +------------+--------------+
                             |
                +------------v--------------+
                | delivery_routing          |
                +------------+--------------+
                             |
                +------------v--------------+
                | delivery_storage          |
                +------------+--------------+
                             |
                +------------v--------------+
                | delivery_complete         |
                +------------+--------------+
                             |
                     +-------v--------+
                     |  validation    |
                     +-------+--------+
                             |
                     +-------v--------+
                     |   complete     |  (terminal)
                     +----------------+

    Any state (except cancelled) -----> cancelled  (terminal)
```

### Transition Rules

Transitions are enforced by `app/services/state_machine.py`. The `ALLOWED_TRANSITIONS` dict is the single source of truth. Any attempt to move to a status not listed as an allowed successor raises `InvalidTransitionError`.

| Current Status | Allowed Next Statuses |
|---|---|
| `intake_pending` | `intake_validated`, `cancelled` |
| `intake_validated` | `engagement`, `cancelled` |
| `engagement` | `solutioning`, `cancelled` |
| `solutioning` | `delivery_collection`, `cancelled` |
| `delivery_collection` | `delivery_routing`, `cancelled` |
| `delivery_routing` | `delivery_storage`, `cancelled` |
| `delivery_storage` | `delivery_complete`, `cancelled` |
| `delivery_complete` | `validation`, `cancelled` |
| `validation` | `complete`, `cancelled` |
| `complete` | `cancelled` |
| `cancelled` | *(none -- terminal)* |

### `transition_request()` Function

```python
def transition_request(
    request: OnboardingRequest,
    new_status: RequestStatus,
    actor: str,
    *,
    action: str | None = None,
    metadata: dict | None = None,
) -> AuditLog:
```

1. Validates the transition against `ALLOWED_TRANSITIONS`.
2. Updates `request.status` and `request.updated_at`.
3. Creates an `AuditLog` row recording the stage, action, actor, and outcome.
4. Returns the audit log entry (caller is responsible for `db.session.commit()`).

## Data Model

### OnboardingRequest

Primary entity representing a single onboarding engagement.

| Column | Type | Description |
|--------|------|-------------|
| `id` | `UUID` (PK) | Auto-generated unique identifier |
| `app_name` | `String(256)` | Human-readable application name |
| `apm_id` | `String(128)`, unique | APM identifier -- natural key for the application |
| `requestor_name` | `String(256)` | Name of the person submitting the request |
| `requestor_email` | `String(320)` | Contact email for the requestor |
| `team` | `String(256)` | Owning team name |
| `environment` | `Enum(dev, stage, prod)` | Target environment for onboarding |
| `status` | `Enum(RequestStatus)` | Current state machine position (default: `intake_pending`) |
| `form_data` | `JSONB` | Free-form intake form responses |
| `entity_mapping` | `JSONB` | Mapping of logical entities to infrastructure resources |
| `workbook_data` | `JSONB` | Solutioning workbook data captured during engagement |
| `created_at` | `DateTime(tz)` | Row creation timestamp (UTC) |
| `updated_at` | `DateTime(tz)` | Last modification timestamp (UTC, auto-updated) |

**Relationships:** `audit_logs` (one-to-many), `delivery_jobs` (one-to-many). Both cascade on delete.

### AuditLog

Immutable record of every state transition and significant action.

| Column | Type | Description |
|--------|------|-------------|
| `id` | `UUID` (PK) | Auto-generated unique identifier |
| `request_id` | `UUID` (FK) | References `onboarding_requests.id` |
| `stage` | `Enum(RequestStatus)` | The status *after* the transition |
| `action` | `String(256)` | Description of the action performed |
| `actor` | `String(256)` | User or system identifier that triggered the action |
| `outcome` | `String(256)` | Result of the action (e.g. `success`, `failed`) |
| `metadata` | `JSONB` | Arbitrary context stored with the log entry |
| `created_at` | `DateTime(tz)` | Timestamp of the log entry (UTC) |

### DeliveryJob

Tracks individual provisioning tasks spawned during the Delivery stage.

| Column | Type | Description |
|--------|------|-------------|
| `id` | `UUID` (PK) | Auto-generated unique identifier |
| `request_id` | `UUID` (FK) | References `onboarding_requests.id` |
| `job_type` | `Enum(cribl_edge, etn_portal, harness_blob)` | Which integration this job targets |
| `status` | `Enum(pending, running, success, failed)` | Current job status |
| `external_ref` | `String(512)` | External system reference ID (e.g. Harness pipeline run ID) |
| `started_at` | `DateTime(tz)` | When execution began |
| `completed_at` | `DateTime(tz)` | When execution finished |
| `result` | `JSONB` | Response payload or error details from the external system |

## Integration Seams

Each service client follows the same pattern: a class with HTTP helper methods (`_get`, `_post`, `_patch`, `_delete`) and domain-specific public methods. All public methods are currently stubs that log the call and return canned responses, so the application can be tested end-to-end without live external services.

### CriblClient

**File:** `app/services/cribl_client.py`

**Purpose:** Configure Cribl Edge agents and create routes/destinations in Cribl Stream.

**Current status:** Stub -- all methods log and return canned dicts.

| Method | Description | Real API Path (planned) |
|--------|-------------|------------------------|
| `configure_edge_agent(app_name, apm_id, config)` | Provision a Cribl Edge agent for the application | TBD -- Edge provisioning API |
| `create_route(worker_group, table, route_payload)` | Create a route in the specified worker group | `POST /api/v1/m/{worker_group}/routes` |
| `create_destination(worker_group, dest_payload)` | Create a destination (e.g. Azure Blob) | `POST /api/v1/m/{worker_group}/system/destinations` |

**To implement:** Replace the stub body with a call to the corresponding `self._post` / `self._patch` helper. The HTTP session, auth headers, and error handling are already wired up.

### ECEClient

**File:** `app/services/ece_client.py`

**Purpose:** Create Elasticsearch security roles, role mappings, and indexes for onboarded applications.

**Current status:** Stub -- all methods log and return canned dicts.

| Method | Description | Real API Path (planned) |
|--------|-------------|------------------------|
| `create_role(name, body)` | Create an ES security role | `PUT /_security/role/{name}` |
| `create_role_mapping(name, body)` | Create an ES role mapping | `PUT /_security/role_mapping/{name}` |
| `create_index(name, body)` | Create an ES index with settings/mappings | `PUT /{name}` |

**To implement:** Replace the stub body with a call to `self._session.put(url, json=body)`. The session and auth are pre-configured.

### ETNPortalClient (planned)

**Purpose:** Sync onboarding status and metadata back to the legacy ETN Portal so both systems stay in sync during the migration period.

**Interface (proposed):**

```python
class ETNPortalClient:
    def update_status(self, apm_id: str, status: str) -> Dict[str, Any]: ...
    def sync_entity_mapping(self, apm_id: str, mapping: dict) -> Dict[str, Any]: ...
```

### HarnessClient (planned)

**Purpose:** Trigger Harness pipelines to provision Azure Blob containers for log storage.

**Interface (proposed):**

```python
class HarnessClient:
    def trigger_pipeline(self, pipeline_id: str, inputs: dict) -> Dict[str, Any]: ...
    def get_execution_status(self, execution_id: str) -> Dict[str, Any]: ...
```

## Delivery Execution Layer

When an onboarding request enters the `delivery_collection` stage, the system creates `DeliveryJob` rows for each provisioning action required:

| Job Type | Service Client | Action |
|----------|---------------|--------|
| `cribl_edge` | `CriblClient` | Configure Edge agent, create route, create destination |
| `etn_portal` | `ETNPortalClient` | Sync status to legacy portal |
| `harness_blob` | `HarnessClient` | Trigger Azure Blob container creation pipeline |

The delivery sub-stages map to logical phases:

1. **`delivery_collection`** -- gather configuration parameters from `entity_mapping` and `workbook_data`.
2. **`delivery_routing`** -- create Cribl routes and destinations.
3. **`delivery_storage`** -- provision Blob storage via Harness.
4. **`delivery_complete`** -- all jobs finished; ready for validation.

Each job transitions through `pending -> running -> success | failed`. The request only advances to the next delivery sub-stage when all jobs for the current phase succeed.

## Audit Trail

Every state transition produces an `AuditLog` entry via `transition_request()`. The audit log captures:

- **Who** performed the action (`actor`)
- **What** happened (`action`, `stage`)
- **When** it happened (`created_at`)
- **Result** (`outcome` -- `success` or `failed`)
- **Context** (`metadata` -- arbitrary JSONB, e.g. error details, input payloads)

Audit logs are immutable: they are insert-only and cascade-deleted only when the parent request is removed. They provide a complete, queryable history of every request's lifecycle.

## Deployment

### Local -- Docker Compose

```bash
docker compose up --build
```

Starts the Flask application and a PostgreSQL instance. Flask-Migrate runs pending migrations on startup.

### Production -- Helm / ARO (Azure Red Hat OpenShift)

```bash
helm install etn-onboarding helm/etn-onboarding/ -n etn-onboarding --create-namespace
```

The Helm chart (in `helm/etn-onboarding/`) defines:

- **Deployment** -- Flask application container with resource limits and environment variable injection
- **Service** -- ClusterIP service exposing the Flask port
- **ConfigMap / Secret** -- environment configuration (DATABASE_URL, API credentials)
- **Liveness / Readiness probes** -- mapped to `/health` and `/ready`
- **PersistentVolumeClaim** -- (if needed) for migration state

PostgreSQL is expected to be provided by an external managed service (e.g. Azure Database for PostgreSQL) in production.

## OpenTelemetry Observability

The service emits traces and structured logs via OpenTelemetry:

- **Tracing:** automatic Flask instrumentation creates spans for each HTTP request. Service client calls are wrapped in child spans so the full request lifecycle (intake through delivery) is visible in a single trace.
- **Structured logging:** JSON-formatted log output includes trace and span IDs, enabling correlation between logs and traces in Elasticsearch / Kibana.
- **Collector endpoint:** configured via the `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable. In the Docker Compose stack, traces flow to the OTel Collector, then to Logstash, and finally to Elasticsearch.
