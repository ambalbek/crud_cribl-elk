# Observability Onboarding Service — Engineering View

**Audience:** platform engineers, architects, reviewers
**Question this answers:** what the system is, how the pieces fit, and what changes from today

---

## 1. C4 Level 1 — System context

```mermaid
flowchart TB
    APPTEAM(["Application Team<br/><i>requester</i>"])
    TST(["TST / Platform Engineer<br/><i>intake analyst, admin</i>"])

    SYS["<b>Observability Onboarding Service</b><br/>Governs onboarding from request<br/>to verified delivery"]

    SP["SharePoint / Confluence<br/><i>request forms, docs</i>"]
    AYS["ServiceNow / AYS<br/><i>queue, incidents</i>"]
    FR["ForgeRock + IIQ / AD<br/><i>SSO, group membership</i>"]
    AKV["Azure Key Vault<br/><i>secrets</i>"]

    CRIBL["Cribl Stream + Edge<br/><i>routes, destinations, fleets</i>"]
    ELK["Elasticsearch + Kibana<br/><i>roles, indexes, ILM, dashboards</i>"]
    DT["Dynatrace<br/><i>zones, host groups, tokens</i>"]
    BLOB["Azure Blob Storage<br/><i>archive containers</i>"]
    HARNESS["Harness<br/><i>provisioning pipelines</i>"]
    GH["GitHub<br/><i>GitOps config repo</i>"]
    MULE["Mulesoft Gateway<br/><i>enterprise API egress</i>"]

    APPTEAM -->|submits request| SP
    APPTEAM -->|checks status| SYS
    TST -->|reviews, approves, operates| SYS

    SP -->|form data| SYS
    SYS <-->|queue, status, incidents| AYS
    SYS -->|authenticates| FR
    SYS -->|reads secrets| AKV

    SYS -->|provisions| CRIBL
    SYS -->|provisions| ELK
    SYS -->|provisions| DT
    SYS -->|triggers| HARNESS
    HARNESS -->|creates| BLOB
    SYS -->|commits configs| GH
    SYS -.->|routed via| MULE

    style SYS fill:#2b6cb0,color:#fff,stroke:#1a365d
```

---

## 2. C4 Level 2 — Containers

Current containers in solid boxes, new containers dashed.

```mermaid
flowchart TB
    subgraph EDGE["Ingress — ARO Route, edge TLS"]
        RT["OpenShift Route"]
    end

    subgraph APP["Application tier"]
        FLASK["<b>cribl-framework</b><br/>Flask 3.1 · :5000<br/>Portal, catalog, intake UI,<br/>solutioning UI, RBAC"]
        WORKER["<b>job worker</b><br/>Celery or RQ<br/>Long-running delivery tasks"]
    end

    subgraph SVC["Integration microservices — FastAPI"]
        CS["<b>cribl_service</b><br/>:8001<br/>Stream + Edge API"]
        ES2["<b>ece_service</b><br/>:8002<br/>ES + Kibana API"]
        DTS["<b>dt_service</b><br/>:8003<br/>Dynatrace API"]
        HS["<b>harness_service</b><br/>:8004<br/>Pipeline trigger + poll"]
    end

    subgraph DATA["State"]
        PG[("<b>Azure PostgreSQL</b><br/>System of record<br/>requests, transitions,<br/>workbooks, tasks")]
        REDIS[("Redis<br/>job queue + cache")]
        ESIDX[("Elasticsearch<br/>read model / legacy index")]
    end

    RT --> FLASK
    FLASK --> WORKER
    FLASK --> PG
    FLASK --> REDIS
    WORKER --> REDIS
    WORKER --> PG

    WORKER --> CS
    WORKER --> ES2
    WORKER --> DTS
    WORKER --> HS

    FLASK -.reads.-> ESIDX

    style DTS stroke-dasharray: 5 5
    style HS stroke-dasharray: 5 5
    style WORKER stroke-dasharray: 5 5
    style PG stroke-dasharray: 5 5
    style REDIS stroke-dasharray: 5 5
```

**What changes from today:** Postgres replaces the Elasticsearch index as system of record; a job
worker moves long-running delivery off the request thread; two new microservices follow the
existing `cribl_service` pattern rather than inventing a new one.

**What stays:** the Flask portal, both existing FastAPI services, the shared modules
(`cribl_api.py`, `cribl_config.py`, `cribl_utils.py`, `cribl_logger.py`, `otel_setup.py`), and both
CLIs. `cribl-pusher.py` and `role_rm.py` get refactored into importable functions with thin
`argparse` wrappers — the flags do not change.

---

## 3. Onboarding lifecycle state machine

```mermaid
stateDiagram-v2
    [*] --> SOURCE_INTAKE

    SOURCE_INTAKE --> SUBMITTED : form normalized,<br/>REQ id assigned
    SOURCE_INTAKE --> REJECTED : malformed / duplicate

    SUBMITTED --> INTAKE_ENGAGEMENT : claimed by analyst
    SUBMITTED --> REJECTED : out of scope

    INTAKE_ENGAGEMENT --> INTAKE_ENGAGEMENT : revision requested
    INTAKE_ENGAGEMENT --> SOLUTIONING : validation passed
    INTAKE_ENGAGEMENT --> REJECTED : withdrawn
    INTAKE_ENGAGEMENT --> ON_HOLD : awaiting customer

    SOLUTIONING --> DELIVERY_EXECUTION : workbook + mappings approved
    SOLUTIONING --> INTAKE_ENGAGEMENT : requirements gap found
    SOLUTIONING --> ON_HOLD : awaiting dependency

    DELIVERY_EXECUTION --> VALIDATION_TURNOVER : all four tasks verified
    DELIVERY_EXECUTION --> FAILED : task failed after retries

    FAILED --> DELIVERY_EXECUTION : retried
    FAILED --> ON_HOLD : escalated

    VALIDATION_TURNOVER --> COMPLETE : customer sign-off
    VALIDATION_TURNOVER --> DELIVERY_EXECUTION : verification gap

    ON_HOLD --> INTAKE_ENGAGEMENT : resumed
    ON_HOLD --> SOLUTIONING : resumed
    ON_HOLD --> REJECTED : abandoned

    COMPLETE --> OFFBOARDED : offboard requested
    OFFBOARDED --> [*]
    REJECTED --> [*]
```

Every transition writes an immutable `StateTransition` row: actor, timestamp, from, to, reason.
The legal-transition table lives in one module and is unit-tested for both legal and illegal moves.
This is the source of all cycle-time measurement.

---

## 4. Delivery Execution Layer

Four sub-tasks, each behind one adapter interface, each independently retryable.

```mermaid
classDiagram
    class DeliveryAdapter {
        <<interface>>
        +plan(request) DiffPreview
        +apply(request) TaskResult
        +verify(request) HealthResult
        +rollback(request) TaskResult
    }

    class CriblEdgeAdapter {
        Edge fleet + agent config
    }
    class DynatraceAgentAdapter {
        OneAgent configuration
    }
    class CriblStreamAdapter {
        Routes, destinations,<br/>apmid allow-list lookups
    }
    class HarnessAdapter {
        Trigger pipeline,<br/>poll execution
    }
    class BlobAdapter {
        Container + lifecycle policy
    }
    class IlmAdapter {
        Index template + ILM tier
    }
    class DynatraceAdapter {
        Management zone,<br/>host group, ingest token
    }
    class MockAdapter {
        Deterministic fixtures<br/>MOCK_INTEGRATIONS=true
    }

    DeliveryAdapter <|.. CriblEdgeAdapter
    DeliveryAdapter <|.. DynatraceAgentAdapter
    DeliveryAdapter <|.. CriblStreamAdapter
    DeliveryAdapter <|.. HarnessAdapter
    DeliveryAdapter <|.. BlobAdapter
    DeliveryAdapter <|.. IlmAdapter
    DeliveryAdapter <|.. DynatraceAdapter
    DeliveryAdapter <|.. MockAdapter
```

Mapping to the four sub-tasks:

| Sub-task | Adapters | Reuses |
|---|---|---|
| Collection | `CriblEdgeAdapter`, `DynatraceAgentAdapter` | `cribl_service` async edge routers |
| Transformation & Routing | `CriblStreamAdapter` | `cribl-pusher.py` logic, route templates, snapshots |
| Storage & Retention | `HarnessAdapter`, `BlobAdapter`, `IlmAdapter` | `blob_dest_template_*.json`, `ece_service` ILM router |
| Endpoint Destination | `DynatraceAdapter` | new — `dt_service` |

### Why `plan / apply / verify / rollback`

The existing framework already has the right instincts — diff preview, dry run, minimum-route
checks, no-shrink guard, rollback snapshots. The interface generalizes those guarantees so every
new integration inherits them instead of reimplementing them. An adapter that cannot produce a
`plan()` diff does not ship.

---

## 5. Delivery sequence

```mermaid
sequenceDiagram
    participant U as Analyst
    participant F as Flask portal
    participant Q as Redis queue
    participant W as Job worker
    participant CS as cribl_service
    participant ES as ece_service
    participant HS as harness_service
    participant DT as dt_service
    participant PG as PostgreSQL

    U->>F: Approve solutioning, start delivery
    F->>PG: transition SOLUTIONING → DELIVERY_EXECUTION
    F->>PG: create 4 DeliveryTask rows
    F->>Q: enqueue 4 jobs
    F-->>U: 202 Accepted, task board URL

    par Collection
        W->>CS: plan edge fleet config
        CS-->>W: diff
        W->>CS: apply
        W->>CS: verify
        W->>PG: task status
    and Transformation and Routing
        W->>CS: plan routes + destinations
        CS-->>W: unified diff
        Note over W,CS: snapshot saved before PATCH
        W->>CS: apply
        W->>CS: verify
        W->>PG: task status
    and Storage and Retention
        W->>HS: trigger blob provisioning pipeline
        HS-->>W: execution id
        loop poll until terminal
            W->>HS: execution status
        end
        W->>ES: apply index template + ILM
        W->>PG: task status
    and Endpoint Destination
        W->>DT: create management zone + host group
        W->>DT: issue ingest token
        W->>PG: task status
    end

    W->>PG: all verified → VALIDATION_TURNOVER
    W->>F: notify
    F-->>U: turnover package ready
```

On any task failure after retries the request moves to `FAILED`, the failing sub-task is recorded,
and an incident is opened in AYS. Successful sibling tasks are **not** rolled back automatically —
partial state is visible and an operator decides. Automatic cascading rollback across four vendor
systems is more dangerous than the partial state it would clean up.

---

## 6. Data model

```mermaid
erDiagram
    ONBOARDING_REQUEST ||--o{ STATE_TRANSITION : "audited by"
    ONBOARDING_REQUEST ||--o| WORKBOOK : "specified by"
    ONBOARDING_REQUEST ||--o{ FIELD_MAPPING : "maps"
    ONBOARDING_REQUEST ||--o{ DELIVERY_TASK : "executes"
    ONBOARDING_REQUEST ||--o{ ARTIFACT : "produces"
    WORKBOOK ||--o{ WORKBOOK_VERSION : "versioned as"

    ONBOARDING_REQUEST {
        uuid id PK
        string request_id UK "REQ-YYYYMMDD-XXXXXXXX"
        string apmid
        string app_name
        string lan_id
        string requester_name
        string region "azn or azs"
        jsonb log_destination "dynatrace, elk"
        jsonb log_type "app logs, metrics"
        jsonb entitlement_groups
        string current_state
        string source_system
        string priority
        timestamp sla_due_at
        timestamp created_at
    }

    STATE_TRANSITION {
        uuid id PK
        uuid request_id FK
        string from_state
        string to_state
        string actor
        string reason
        timestamp occurred_at
    }

    WORKBOOK {
        uuid id PK
        uuid request_id FK
        int current_version
        string retention_tier
        numeric expected_gb_per_day
        jsonb environments
    }

    FIELD_MAPPING {
        uuid id PK
        uuid request_id FK
        string source_field
        string normalized_field
        string data_type
        jsonb tags
        bool pii_flag
    }

    DELIVERY_TASK {
        uuid id PK
        uuid request_id FK
        string sub_task "collection, routing, storage, endpoint"
        string status
        string external_job_id
        jsonb request_payload
        jsonb response_payload
        int attempts
    }

    ARTIFACT {
        uuid id PK
        uuid request_id FK
        string kind "route, destination, role, ilm, container, dashboard"
        string external_id
        jsonb definition
        string url
    }
```

`apmid` is indexed as a first-class column. This removes an entire class of bug that exists today —
the ES index maps `apmid` as `text`, which is why `_update_by_query` on `apmid.keyword` silently
returns `updated: 0` when the index template has not been applied.

---

## 7. Migration strategy — Elasticsearch to PostgreSQL

```mermaid
flowchart LR
    S1["<b>Stage 1</b><br/>ES authoritative<br/>STORE_BACKEND=es"] --> S2["<b>Stage 2</b><br/>Dual write<br/>STORE_BACKEND=dual<br/>ES still authoritative"]
    S2 --> S3["<b>Stage 3</b><br/>Backfill script<br/>historical ES docs → PG"]
    S3 --> S4["<b>Stage 4</b><br/>Reconcile + validate<br/>diff report until clean"]
    S4 --> S5["<b>Stage 5</b><br/>PG authoritative<br/>STORE_BACKEND=postgres<br/>ES becomes read model"]
    S5 --> S6["<b>Stage 6</b><br/>Retire ES writes"]

    S4 -.rollback.-> S2

    style S5 fill:#f0fff4,stroke:#2f855a
    style S4 fill:#fffaf0,stroke:#dd6b20
```

Stage 4 is the gate. Do not advance until the reconciliation diff is empty across a full
onboarding cycle. The rollback path from Stage 4 back to Stage 2 is a single config flag —
that is the entire reason for the dual-write stage.

---

## 8. Deployment topology on ARO

```mermaid
flowchart TB
    subgraph AZURE["Azure"]
        subgraph ARO["Azure Red Hat OpenShift"]
            subgraph NS["namespace: obs-onboarding"]
                ROUTE["Route<br/>edge TLS"]
                DEP1["Deployment<br/>cribl-framework<br/>HPA 2-6"]
                DEP2["Deployment<br/>worker<br/>HPA 2-8"]
                DEP3["Deployment<br/>cribl_service"]
                DEP4["Deployment<br/>ece_service"]
                DEP5["Deployment<br/>dt_service"]
                DEP6["Deployment<br/>harness_service"]
                REDIS["StatefulSet<br/>redis"]
                ESO["ExternalSecret<br/>→ Key Vault"]
                SA["ServiceAccount<br/>workload identity"]
                NP["NetworkPolicy<br/>default deny"]
                JOB["pre-deploy Job<br/>alembic upgrade head"]
            end
        end

        PG[("Azure Database<br/>for PostgreSQL")]
        AKV["Azure Key Vault"]
        BLOBS["Blob Storage<br/>archive containers"]
    end

    NEXUS["Nexus Container Registry"]
    GHA["GitHub Actions<br/>lint · mypy · pytest ·<br/>helm lint · trivy · build"]
    HARN["Harness<br/>promote · migrate ·<br/>deploy · smoke · rollback"]

    GHA -->|push image by digest| NEXUS
    NEXUS --> HARN
    HARN -->|helm upgrade| NS
    JOB --> PG
    DEP1 --> PG
    DEP2 --> PG
    ESO -.->|workload identity| AKV
    SA -.-> AKV
    DEP2 --> BLOBS

    style JOB stroke-dasharray: 5 5
```

Container requirements for OpenShift specifically: non-root, arbitrary UID tolerant, group 0 write
permissions on any writable path, no `USER 0`, no assumptions about a fixed UID in the Dockerfile.
The current `python:3.13-slim` image will need adjusting.

---

## 9. Security model

```mermaid
flowchart LR
    USER(["User"]) -->|OIDC PKCE| FR["ForgeRock"]
    FR -->|id_token + groups| FLASK["Flask portal"]
    IIQ["IIQ / Active Directory"] -->|group membership| FR

    FLASK -->|role mapping| ROLES["requester<br/>intake_analyst<br/>admin<br/>read_only"]

    FLASK -->|short-lived S2S token| SVCS["FastAPI services"]
    SVCS -->|workload identity| AKV["Azure Key Vault"]
    AKV -->|Cribl, ES, DT, Harness,<br/>Postgres credentials| SVCS

    style AKV fill:#2b6cb0,color:#fff
```

**This replaces local accounts in `config.json` entirely.** Today the config file holds usernames
and plaintext passwords for both admin and user roles, plus Cribl and Elasticsearch credentials.
Preserve the existing page-access matrix during cutover — map the current `user` role to
`requester` and `admin` to `admin` — but the credential store itself goes away.

Config after this change holds **secret names, never secret values.**

---

## 10. Observability of the service itself

The existing OTLP path stays and is extended:

```
app services ──OTLP──> Cribl Edge ──> Logstash ──> Elasticsearch ──> Kibana
                            │
                            └──> optional direct routing
```

Additions:
- Trace context propagated across Flask → queue → worker → adapter → vendor API. The `request_id`
  becomes a span attribute so a single onboarding is one trace end to end.
- Prometheus `/metrics` per service: delivery task duration by adapter, failure count by adapter,
  queue depth, state-transition counts.
- `/healthz` and `/readyz` split — readiness gates on Postgres and Redis, liveness does not.
- Structured JSON logs with `request_id`, `apmid`, `trace_id`, `span_id` on every line.

---

## 11. Testing strategy

| Layer | What | Runs where |
|---|---|---|
| Unit | State machine legal/illegal transitions, template rendering, diff logic | CI, every commit |
| Contract | Each adapter against its mock; response shape assertions | CI, every commit |
| Integration | Full stack with `MOCK_INTEGRATIONS=true`, real Postgres and Redis | CI, every commit |
| Migration | Alembic upgrade + downgrade against a seeded database | CI, every commit |
| Smoke | Post-deploy health and one dry-run onboarding | Harness, every deploy |
| Manual | Real dry-run against non-prod Cribl and ELK | Before prod promotion |

The mock adapters are what make CI meaningful. Without them nothing beyond unit tests can run
without live vendor systems, which is the current situation.

---

## 12. Sequencing rationale

```mermaid
flowchart LR
    P0["Phase 0<br/>Audit"] --> P1["Phase 1<br/>Postgres +<br/>state machine"]
    P1 --> P2["Phase 2<br/>Delivery<br/>adapters"]
    P2 --> P3["Phase 3<br/>Intake +<br/>solutioning"]
    P3 --> P4["Phase 4<br/>SSO +<br/>Key Vault"]
    P4 --> P5["Phase 5<br/>ARO +<br/>CI/CD"]
    P5 --> P6["Phase 6<br/>Docs"]

    P0 -.->|"credential rotation<br/>if history is dirty"| URGENT["Immediate"]

    style URGENT fill:#fff5f5,stroke:#c53030
    style P1 fill:#ebf8ff,stroke:#2b6cb0
```

Phase 1 is the keystone: the state machine is what makes delivery orchestratable and what makes
every business metric in the leadership deck measurable. Phase 2 depends on `DeliveryTask` rows
existing. Phase 3 depends on `Workbook` and `FieldMapping`. Nothing meaningful can be sequenced
before it.

Phase 0 can surface work that jumps the queue. If credentials are in git history, rotation happens
before anything else ships.
