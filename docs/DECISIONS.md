# Phase 0 — Architecture Decisions

> Generated 2026-07-27. Each section states the question, the options considered,
> a recommendation, and the tradeoffs.

---

## 1. System of record during migration: ES → Postgres

### Question
How do we migrate the onboarding request store from Elasticsearch to Postgres without downtime or data loss?

### Options

**A. Dual-write with cutover (recommended)**
- Add Postgres behind a `STORE_BACKEND` flag: `es` (default) → `dual` → `postgres`.
- In `dual` mode, every write goes to both stores; reads come from ES.
- Run a one-time backfill script to copy existing ES docs into Postgres.
- Validate parity, then flip reads to Postgres.
- Finally, set `STORE_BACKEND=postgres` and stop writing to ES.

**B. Big-bang migration**
- Freeze writes, run backfill, switch all code to Postgres in one deploy.

### Recommendation: **Option A — dual-write with cutover**

**Why A:**
- Zero-downtime migration. The portal never goes offline.
- If Postgres has issues, flip back to `es` immediately.
- The backfill script doubles as a validation tool (compare counts, spot-check docs).
- Aligns with the phased approach (Phase 1 adds Postgres; Phase 2+ can depend on it).

**Why not B:**
- The portal is in operational use. A freeze window requires coordination with intake analysts.
- If the backfill or migration has a bug, rollback is messy (which store is authoritative?).

**Risk with A:**
- Dual-write adds latency (~1 Postgres INSERT per request). Acceptable for this volume (<100 requests/day).
- Must handle the case where one write succeeds and the other fails (log + retry, not crash).

---

## 2. AYS vs. this service as source of truth

### Question
ETN portal currently owns the onboarding form. The target is AYS (ServiceNow). During migration, which system is authoritative?

### Options

**A. This service stays authoritative; AYS is a feeder (recommended)**
- AYS submits via webhook or API into this service's `OnboardingRequest` table.
- This service owns the state machine, delivery tasks, and lifecycle.
- AYS gets status write-backs so the ITSM record stays current.

**B. AYS is authoritative; this service is an executor**
- This service polls AYS for new requests and pushes delivery results back.
- AYS owns state transitions.

### Recommendation: **Option A — this service authoritative**

**Why A:**
- The delivery logic (Cribl, ELK, Harness, Dynatrace) is tightly coupled to this codebase. Putting the state machine here means delivery tasks can transition states locally without round-tripping to ServiceNow.
- AYS is a shared enterprise platform — adding custom states/transitions there requires change management and governance approval. Doing it here is faster and more controllable.
- Multiple intake channels (SharePoint, Confluence, AYS, portal form) all funnel into one store.

**Why not B:**
- AYS as authoritative means every state change must pass through ServiceNow API, adding latency, coupling, and a dependency on AYS availability for delivery execution.
- AYS doesn't model the sub-task granularity we need (Collection, Routing, Storage, Endpoint).

**Migration path:**
- Feature-flag the intake source: `INTAKE_SOURCE=portal` (today) → `INTAKE_SOURCE=ays` (target).
- During transition, both portal form and AYS webhook write to `OnboardingRequest`.
- When AYS is fully adopted, disable the portal form and make AYS the sole intake feeder.

---

## 3. Multi-tenancy: single DB with tenant column vs. schema-per-BU

### Options

**A. Single Postgres DB, `tenant` / `business_unit` column on key tables (recommended)**

**B. Schema-per-BU** (e.g., `bu_claims.onboarding_requests`, `bu_pharmacy.onboarding_requests`)

**C. Database-per-BU**

### Recommendation: **Option A — single DB, tenant column**

**Why A:**
- Simplest operationally. One connection string, one migration path, one backup schedule.
- The current ES index is already single-tenant (all requests in one index). Moving to a column is the smallest change.
- Cross-BU queries (catalog, reporting, audit) are trivial — just remove the WHERE clause.
- Row-level security in Postgres can enforce tenant isolation if needed later.
- Expected total volume is small (thousands of requests, not millions).

**Why not B:**
- Schema-per-BU adds migration complexity (must run Alembic per schema).
- Cross-BU queries require UNION or schema iteration.
- Overkill for the expected data volume and access patterns.

**Why not C:**
- Maximum isolation but maximum operational burden. No business justification at this scale.

**How to apply:**
- Add a `business_unit` VARCHAR column to `OnboardingRequest`.
- Default to a single BU initially; populate from the intake form's org field.
- Add a composite index on `(business_unit, current_state)` for queue filtering.

---

## 4. Retention tier taxonomy and mapping to blob lifecycle + ES ILM

### Question
How do we define retention tiers, and how do they map to Azure Blob lifecycle policies and Elasticsearch ILM?

### Recommendation

Define three standard tiers (expandable):

| Tier | Blob hot | Blob cool | Blob archive | Blob delete | ES hot | ES warm | ES cold | ES delete |
|---|---|---|---|---|---|---|---|---|
| `standard` | 30 d | 90 d | 1 y | 7 y | 30 d | 90 d | 1 y | 7 y |
| `extended` | 90 d | 1 y | 3 y | 10 y | 90 d | 1 y | 3 y | 10 y |
| `compliance` | 1 y | 3 y | 7 y | never | 1 y | 3 y | 7 y | never |

**How it maps:**
- Each tier name → one Azure Blob lifecycle management rule (applied to the container or prefix).
- Each tier name → one ES ILM policy (applied via index template).
- The `Workbook` entity stores the selected tier. Delivery tasks reference it when creating blob containers and ES index templates.
- The `IlmAdapter` (Phase 2) creates or updates the ILM policy on the target cluster.
- The `BlobAdapter` (Phase 2) tags the container with the tier name; the lifecycle rule matches on the tag.

**Tradeoffs:**
- Fixed tiers are simpler to manage than arbitrary per-app retention. Custom retention can be modeled as a new tier if needed.
- "Never delete" in compliance tier must be enforced by policy (no automated deletion job should touch it). Add a safety check in the `IlmAdapter`.
- The tier taxonomy should be stored in config (not hardcoded) so it can evolve without code changes.

---

## 5. Whether to keep the ES onboarding index after Postgres cutover

### Options

**A. Keep ES as a read model (recommended for near-term)**
- After Postgres becomes authoritative, stop writing to ES.
- Keep the existing index read-only for backward compatibility (catalog queries, Kibana dashboards, existing scripts).
- Set an ILM policy to eventually delete it (e.g., 6 months after cutover).

**B. Delete the ES index immediately after cutover**
- Simpler — one store to maintain.
- Breaks any Kibana dashboards or scripts that query the index.

**C. Keep ES as a permanent read model (event-sourced)**
- Treat ES as a search/analytics layer fed by Postgres change-data-capture (CDC).
- Ongoing operational cost but enables full-text search and Kibana analytics.

### Recommendation: **Option A — keep read-only, sunset after 6 months**

**Why A:**
- Low risk. The index exists, it's small, and keeping it read-only costs nothing.
- Gives time to migrate any Kibana dashboards or reporting queries to Postgres.
- The 6-month sunset provides a clear deadline to remove the ES dependency.

**Why not B:**
- Breaking existing dashboards/scripts during migration adds unnecessary risk and coordination cost.

**Why not C:**
- CDC adds significant infrastructure complexity (Debezium or similar).
- The onboarding data is low-volume and doesn't benefit from ES's search capabilities.
- If full-text search is needed later, Postgres `tsvector` or a simple LIKE query suffices for this volume.

**How to apply:**
- When `STORE_BACKEND=postgres`, stop writing to ES.
- Add a `LEGACY_ES_READ=true` flag that the catalog endpoint checks. When true, it merges ES results with Postgres results (dedup by request_id). When false, Postgres only.
- Set a calendar reminder to flip `LEGACY_ES_READ=false` and delete the index 6 months post-cutover.
