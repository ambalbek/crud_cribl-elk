# Cribl Framework

Unified platform for application onboarding into **Cribl Stream** and **ELK**. The system is composed of **three containerised services**:

| Service | Stack | Port | Role |
|---|---|---|---|
| `cribl-framework` | Flask 3.1 | 5000 | Web portal — onboarding form, Cribl Pusher UI, Entitlement Lookup, Service Catalog, RBAC |
| `cribl_service` | FastAPI + uvicorn | 8001 | Cribl Stream API — routes, destinations, pipelines, worker groups, leaders, edge fleets |
| `ece_service` | FastAPI + uvicorn | 8002 | ECE/ELK API — ES roles, role-mappings, indexes, ILM policies, Logstash pipelines, Kibana dashboards |

`cribl-framework` is the **consumer** of `cribl-service`. It calls the service's REST API instead of invoking CLI subprocesses directly.

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [End-to-End Workflow](#end-to-end-workflow)
3. [Prerequisites](#prerequisites)
4. [File Structure](#file-structure)
5. [First-Time Setup](#first-time-setup)
6. [Configuration Reference](#configuration-reference)
7. [cribl_service — Cribl Stream API](#cribl_service--cribl-stream-api)
8. [ece_service — ECE / ELK API](#ece_service--ece--elk-api)
9. [Service Catalog](#service-catalog)
10. [Template Files](#template-files)
11. [App Input Format](#app-input-format)
12. [Running the Application](#running-the-application)
13. [Web UI](#web-ui)
14. [role_rm.py — ELK Roles + Cribl](#role_rmpy--elk-roles--cribl)
15. [Docker](#docker)
16. [Serving via Apache httpd (bastion)](#serving-via-apache-httpd-bastion)
17. [All CLI Flags](#all-cli-flags)
18. [Logging](#logging)
19. [Safety Features](#safety-features)
20. [Rolling Back a Change](#rolling-back-a-change)
21. [Troubleshooting](#troubleshooting)

---

## What It Does

### Onboarding Portal

Clients submit structured onboarding requests via a web form:

- **LAN ID** and **Name / Last Name** of the requester
- **APM ID** and **App Name** of the application
- **Region** (Azure North or Azure South)
- **Log Destination** (Dynatrace and/or ELK)
- **Log Type** (Application Logs and/or Metrics)
- **Entitlement Groups** (AD groups for access)

Each submission is stored as a document in an Elasticsearch index with a unique **Request ID** (`REQ-YYYYMMDD-XXXXXXXX`).

### Cribl Pusher

For each application you provide (by ID and name), the script:

1. Fetches the current route table from Cribl (`GET /api/v1/m/{worker_group}/routes/{routes_table}`)
2. Fetches all existing destinations (`GET /system/outputs`) to build a skip-list
3. Inserts a new route above the catch-all/default route — skipping any that already exist
4. Shows a full unified diff so you can review exactly what will change
5. Asks for confirmation before writing anything
6. Saves a rollback snapshot of the original route table
7. Creates any destination that does not already exist (`POST /system/outputs`) — skips if present
8. Patches the route table back to Cribl (`PATCH /api/v1/m/{worker_group}/routes/{routes_table}`)

### ELK Roles + Cribl Routes (role_rm.py)

`role_rm.py` applies **ELK roles/role-mappings** and **Cribl routes/destinations** in a single command:

1. Generates ELK role and role-mapping templates (always saved to `ops_rm_r_templates_output/`)
2. Pushes roles and role-mappings to Elasticsearch via `PUT /_security/role/{name}` and `PUT /_security/role_mapping/{name}`
3. Runs the same route + destination upsert logic as `cribl-pusher.py`
4. Runs the two sides in the configured order (`elk-first` by default)

### Entitlement Lookup

Browse entitlement-to-role mappings across all configured Elasticsearch clusters:

1. Connects to each ES cluster and fetches `/_security/role_mapping`
2. Extracts entitlement groups (DNs) matching the configured filter text
3. Displays results in a searchable, sortable table with:
   - Global search and per-column filters
   - Cluster and status dropdown filters
   - Pagination (50/100/250/500/All rows per page)
   - CSV export of filtered results
4. Shows cluster name, entitlement CN, full DN, role mapping name, assigned roles, and enabled status

### Automatic Status Update

After a successful run (non-dry-run), the framework **automatically updates the onboarding request status to `done`** in the Elasticsearch index. The operator simply pastes the `REQ-YYYYMMDD-XXXXXXXX` ID into the Portal Request ID field before running.

### Authentication & Access Control (RBAC)

The framework uses **local account authentication** with role-based access control:

- Users sign in with **username and password** via a login page
- **Roles** (admin / user) are configured in `config.json` under `auth.local_admins` and `auth.local_users`
- **Session management** uses Flask's signed-cookie sessions (configurable lifetime)

**Page access matrix:**

| Page | User Role | Admin Role |
|------|-----------|------------|
| `/cribl/login`, `/cribl/logout` | Public | Public |
| `/cribl/health`, `/cribl/health/es` | Public | Public |
| `/cribl/` (landing page) | Redirects to `/cribl/portal` | Full dashboard |
| `/cribl/portal` (onboarding form) | Yes | Yes |
| `/cribl/entitlements` (lookup) | Yes | Yes |
| `/cribl/catalog` (service catalog) | Yes (read-only) | Yes (+ offboard/re-onboard) |
| `/cribl/app` (Pusher) | No | Yes |
| `/cribl/portal/admin/update-status` | No | Yes |

**Accounts** are configured in `config.json`:

```json
"auth": {
  "session_lifetime_minutes": 480,
  "local_admins": [
    { "username": "admin", "password": "your_password", "display_name": "Local Admin" }
  ],
  "local_users": [
    { "username": "user", "password": "user123", "display_name": "Test User" }
  ]
}
```

---

## End-to-End Workflow

```
0. User visits any page → Redirected to /cribl/login
   → Enters username + password → Credentials validated
   → Role assigned (user or admin) → Session created
   → User role → Portal + Entitlements | Admin role → All pages

1. Client opens the Onboarding Portal (/cribl/portal)
   → Username and name auto-populated from session
   → Fills in App ID, App Name, Region, Log Destination, Log Type, Entitlement Groups
   → Receives Request ID: REQ-20260327-A1B2C3D4

2. Platform team opens Cribl Pusher (/cribl/app)
   → Pastes REQ-20260327-A1B2C3D4 in "Portal Request ID"
   → Selects workspace, worker group(s), region
   → Enters app details (or uploads bulk file)
   → Runs with Dry Run first to preview changes

3. Unchecks Dry Run, clicks Run
   → Routes created in Cribl
   → Destinations created in Cribl
   → ELK roles/role-mappings created (if using rode_rm)
   → Request status auto-updated to "done" in Elasticsearch

4. Client's request is marked as completed

5. (Optional) Verify entitlements via Entitlement Lookup (/entitlements)
   → Browse role mappings across all ELK clusters
   → Search, filter, sort, and export to CSV
```

---

## Prerequisites

- **Python 3.10 or newer** *(not needed if running via Docker)*
- **Docker Desktop** *(optional — for the containerised option)*
- **pip** packages:

```bash
pip install -r requirements.txt
```

Verify your Python version:

```bash
python --version
# Should print Python 3.10.x or higher
```

---

## File Structure

```
cribl-framework-ent/
│
├── app.py                          # Flask portal — onboarding, pusher UI, catalog, entitlements, RBAC
├── cribl-pusher.py                 # CLI — add routes + upsert destinations (also used by cribl_service)
├── role_rm.py                      # CLI — ELK roles + Cribl routes together
├── _validate.py                    # Offline validation script
│
├── cribl_api.py                    # Shared — Cribl route logic (normalize, diff, unwrap)
├── cribl_config.py                 # Shared — config loading and workspace resolution
├── cribl_utils.py                  # Shared — utilities (I/O, prompts, HTTP session)
├── cribl_logger.py                 # Shared — logging setup
│
├── Dockerfile                      # cribl-framework image (python:3.13-slim, port 5000)
├── docker-compose.yml              # Three services: cribl-framework :5000, cribl_service :8001, ece_service :8002
├── requirements.txt                # Flask service dependencies
│
├── config.json                     # YOUR config (credentials + workspaces) — never commit
├── config.example.json             # Safe-to-commit template
│
├── route_template_azn.json         # Route shape for Azure North
├── route_template_azs.json         # Route shape for Azure South
├── blob_dest_template_azn_dev.json # Dest shape — AZN dev
├── blob_dest_template_azs_dev.json # Dest shape — AZS dev
├── blob_dest_template_azn_test.json
├── blob_dest_template_azs_test.json
├── blob_dest_template_azn_prod.json
├── blob_dest_template_azs_prod.json
│
├── elk-index-template.json         # ES index template for onboarding requests
├── elk-role.json                   # ES role for portal writer
│
├── templates/                      # Jinja2 templates for cribl-framework
│   ├── index.html
│   ├── request.html
│   ├── admin.html
│   ├── app.html
│   ├── entitlements.html
│   ├── catalog.html                # Service Catalog dashboard
│   └── login.html
│
├── ece_service/                    # FastAPI microservice — ECE/ELK API (port 8002)
│   ├── __init__.py
│   ├── main.py                     # App factory, router registration, /health
│   ├── deps.py                     # ECEClient — sync requests-based ES + Kibana client
│   ├── ece_client.py               # AsyncECEClient — async httpx client (ILM, index templates)
│   ├── config.py                   # pydantic-settings ECESettings (ECE_ prefix)
│   ├── models.py                   # Pydantic v2 models: roles, ILM, Logstash, Kibana, provision
│   ├── settings.py                 # Legacy env-var settings (os.environ)
│   ├── requirements.txt            # fastapi, uvicorn, requests, urllib3, jinja2, httpx, pydantic-settings
│   ├── Dockerfile                  # Build context = project root (copies role_rm.py + shared .py)
│   ├── .env.example                # Template for ECE_* environment variables
│   └── routers/
│       ├── roles.py                # ES security roles CRUD + /generate + /provision
│       ├── role_mappings.py        # ES security role-mappings CRUD
│       ├── indexes.py              # ES index + index-template CRUD
│       ├── ilm.py                  # ILM policy CRUD + /explain (async)
│       ├── logstash_pipelines.py   # Logstash pipelines stored in ES
│       └── kibana_dashboards.py    # Kibana dashboards (saved objects API)
│
├── cribl_service/                  # FastAPI microservice — Cribl Stream API (port 8001)
│   ├── __init__.py
│   ├── main.py                     # App factory, router registration, /health
│   ├── deps.py                     # CriblClient — sync requests-based authenticated client
│   ├── cribl_client.py             # AsyncCriblClient — async httpx client (stream/edge/workgroups)
│   ├── config.py                   # pydantic-settings CriblSettings (CRIBL_ prefix)
│   ├── models.py                   # Pydantic v2 models: routes, destinations, fleets, workgroups, pipelines
│   ├── settings.py                 # Legacy env-var settings (os.environ)
│   ├── requirements.txt            # fastapi, uvicorn, requests, urllib3, httpx, pydantic-settings
│   ├── Dockerfile                  # Build context = project root (copies shared .py files)
│   ├── .env.example                # Template for CRIBL_* environment variables
│   └── routers/
│       ├── routes.py               # GET/PATCH table, POST route (smart insert), DELETE route
│       ├── destinations.py         # Full CRUD for outputs (/system/outputs)
│       ├── pipelines.py            # Full CRUD for pipelines
│       ├── worker_groups.py        # Full CRUD for worker groups (sync)
│       ├── leaders.py              # GET system info + git settings, PATCH git settings
│       ├── stream.py               # Semantic routes + destinations CRUD (async)
│       ├── edge.py                 # Edge fleet CRUD (async)
│       ├── workgroups.py           # Worker group CRUD (async)
│       └── provision.py            # Bulk idempotent route+destination provisioning
│
├── ops_rm_r_templates_output/      # Auto-created by role_rm.py
│
└── cribl_snapshots/                # Auto-created — rollback snapshots
    ├── dev/
    ├── test/
    └── prod/
```

> `config.json` and `cribl_snapshots/` are in `.gitignore` and will never be committed.

---

## First-Time Setup

### Step 1 — Clone / copy the files

Make sure all `.py` files, template `.json` files, and `config.example.json` are in the same folder.

### Step 2 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 3 — Create your config file

```bash
# Windows
copy config.example.json config.json

# Mac / Linux
cp config.example.json config.json
```

### Step 4 — Edit config.json

Open `config.json` and fill in your values. See [Configuration Reference](#configuration-reference) for all fields.

### Step 5 — Apply the ES index template (for the portal)

```bash
curl -k -X PUT "https://YOUR_ELK:9200/_index_template/cribl-onboarding-requests" \
  -H "Content-Type: application/json" \
  -d @elk-index-template.json
```

### Step 6 — Do a dry run

```bash
python cribl-pusher.py --workspace dev --worker-group default --region azn --dry-run --appid TEST001 --appname "Test App"
```

You should see the `=== TARGET ===` banner and a diff preview with no errors. **Nothing is written on a dry run.**

---

## Configuration Reference

### Top-level fields

| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | — | Default Cribl root URL (overridden per workspace or via `--cribl-url`) |
| `cribl_urls` | list | `[]` | Cribl URLs shown as a dropdown in the UI |
| `elk_urls` | list | `[]` | ELK URLs shown as a dropdown in the UI |
| `skip_ssl` | bool | `false` | Disable SSL cert verification globally |
| `credentials.token` | string | `""` | Bearer token — if set, skips username/password login |
| `credentials.username` | string | `""` | Login username |
| `credentials.password` | string | `""` | Login password |
| `route_templates` | object | — | Map of region to route template path |
| `dest_prefixes` | object | — | Map of region to destination ID prefix |
| `snapshot_dir` | string | `cribl_snapshots` | Directory where rollback snapshots are saved |
| `min_existing_total_routes` | int | `1` | Refuse to PATCH if fewer than this many routes are loaded |
| `diff_lines` | int | `3` | Lines of context shown in the diff preview |
| `admin_secret` | string | — | Secret for the admin status update API |
| `secret_key` | string | — | Flask session signing key (generate a random string) |
| `auth.session_lifetime_minutes` | int | `480` | Session cookie lifetime in minutes (default 8 hours) |
| `auth.local_admins` | list | `[]` | Admin accounts (username, password, display_name) — full access |
| `auth.local_users` | list | `[]` | User accounts (username, password, display_name) — Portal + Entitlements only |
| `entitlement.clusters` | list | `[]` | Elasticsearch clusters for entitlement lookup (see below) |
| `entitlement.entitlementFilter` | string | `"entitlements"` | Substring to match in role mapping rules |
| `datastream.elk_url` | string | — | Elasticsearch URL for the onboarding requests index |
| `datastream.token` | string | `""` | ES API key (base64) — overrides username/password |
| `datastream.username` | string | `""` | ES username (basic auth) |
| `datastream.password` | string | `""` | ES password (basic auth) |
| `datastream.index` | string | `cribl-onboarding-requests` | ES index name |
| `datastream.skip_ssl` | bool | `false` | Disable SSL for ES connections |
| `datastream.timeout` | int | `30` | ES request timeout in seconds |

### Workspace fields

Each key under `workspaces` is a name you choose (e.g. `"dev"`, `"prod"`).

| Field | Required | Description |
|---|---|---|
| `worker_groups` | yes | List of Cribl worker group names (e.g. `["default", "wg-dev-01"]`) |
| `dest_templates` | yes* | Object mapping region to dest template path |
| `dest_template` | yes* | Alternative: single dest template path (skips region lookup) |
| `base_url` | no | Overrides global `base_url` for this workspace |
| `routes_table` | no | Route table name. Defaults to `"default"` |
| `description` | no | Human-readable label shown in the UI |
| `require_allow` | no | If `true`, user must confirm before writes (recommended for prod) |
| `skip_ssl` | no | Overrides global `skip_ssl` for this workspace |

*One of `dest_templates` or `dest_template` is required.

### Entitlement clusters

Each entry under `entitlement.clusters` defines an Elasticsearch cluster to query for role mappings:

| Field | Required | Description |
|---|---|---|
| `name` | yes | Display name for the cluster (e.g. `"production"`) |
| `url` | yes | Full URL to Elasticsearch (e.g. `"https://elk-prod:9200"`) |
| `username` | yes | Basic auth username |
| `password` | yes | Basic auth password |

Example:

```json
"entitlement": {
  "clusters": [
    { "name": "production", "url": "https://elk-prod:9200", "username": "elastic", "password": "..." },
    { "name": "staging",    "url": "https://elk-stg:9200",  "username": "elastic", "password": "..." }
  ],
  "entitlementFilter": "entitlements"
}
```

The `entitlementFilter` value is matched (case-insensitive substring) against DN and group fields in Elasticsearch role mapping rules.

### Credential priority (highest to lowest)

```
1. --token / --username / --password  CLI flags
2. CRIBL_TOKEN / CRIBL_USERNAME / CRIBL_PASSWORD  environment variables
3. credentials block in config.json
```

---

## cribl_service — Cribl Stream API

`cribl_service` is a FastAPI microservice that wraps the Cribl Stream REST API. `cribl-framework` (Flask) calls it over HTTP instead of running CLI subprocesses.

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `CRIBL_BASE_URL` | yes | Cribl root URL, e.g. `http://cribl:9000` |
| `CRIBL_TOKEN` | one of | Bearer token (takes priority over username/password) |
| `CRIBL_USERNAME` | one of | Cribl login username |
| `CRIBL_PASSWORD` | one of | Cribl login password |
| `CRIBL_SKIP_SSL` | no | `true` to disable SSL verification (default: `false`) |
| `CRIBL_TIMEOUT` | no | HTTP timeout in seconds (default: `30`) |
| `CRIBL_DEFAULT_WORKSPACE` | no | Default workspace name (default: `default`) |
| `CRIBL_DEFAULT_ROUTES_TABLE` | no | Default route table (default: `default`) |
| `CRIBL_MIN_EXISTING_ROUTES` | no | Safety gate — minimum routes before patching (default: `1`) |
| `CRIBL_SNAPSHOT_DIR` | no | Snapshot directory (default: `cribl_snapshots`) |
| `LOG_LEVEL` | no | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: `INFO`) |

See `cribl_service/.env.example` for a ready-to-copy template.

### API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| **Classic routes (sync)** | | |
| `GET` | `/api/v1/m/{wg}/routes/{table}` | Fetch full route table |
| `PATCH` | `/api/v1/m/{wg}/routes/{table}` | Replace full route table |
| `POST` | `/api/v1/m/{wg}/routes/{table}/route` | Add one route (inserts above final:true) |
| `DELETE` | `/api/v1/m/{wg}/routes/{table}/route/{name}` | Remove route by name or id |
| `GET/POST/PATCH/DELETE` | `/api/v1/m/{wg}/destinations/{id}` | Destinations CRUD |
| `GET/POST/PATCH/DELETE` | `/api/v1/m/{wg}/pipelines/{id}` | Pipelines CRUD |
| `GET/POST/PATCH/DELETE` | `/api/v1/worker-groups/{id}` | Worker groups CRUD |
| `GET` | `/api/v1/leaders/info` | System info (version, build) |
| `GET/PATCH` | `/api/v1/leaders/settings/git` | Git settings CRUD |
| `POST` | `/api/v1/m/{wg}/provision` | Bulk idempotent route+destination upsert |
| **Stream (async)** | | |
| `GET/POST/PATCH/DELETE` | `/cribl/stream/routes/{app_id}?worker_group=` | Semantic route CRUD with snapshot + idempotency |
| `GET/POST/PATCH/DELETE` | `/cribl/stream/destinations/{dest_id}?worker_group=` | Semantic destination CRUD |
| **Edge (async)** | | |
| `GET/POST/PATCH/DELETE` | `/cribl/edge/fleets/{id}` | Edge fleet CRUD |
| **Workgroups (async)** | | |
| `GET/POST/PATCH/DELETE` | `/cribl/workgroups/{id}` | Worker group CRUD (async) |

Interactive docs: `http://localhost:8001/docs`

---

## ece_service — ECE / ELK API

`ece_service` is a FastAPI microservice wrapping Elasticsearch and Kibana APIs. It re-uses the template generation and push logic from `role_rm.py` and adds full CRUD for indexes, Logstash pipelines, and Kibana dashboards.

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `ECE_ES_URL` | yes | Elasticsearch nonprod URL, e.g. `https://elk-stg:9200` |
| `ECE_ES_TOKEN` | one of | ApiKey for nonprod ES (base64, overrides user/pass) |
| `ECE_ES_USERNAME` | one of | Nonprod ES username |
| `ECE_ES_PASSWORD` | one of | Nonprod ES password |
| `ECE_ES_URL_PROD` | for provision | Elasticsearch prod URL |
| `ECE_ES_TOKEN_PROD` | one of | ApiKey for prod ES |
| `ECE_ES_USERNAME_PROD` | one of | Prod ES username |
| `ECE_ES_PASSWORD_PROD` | one of | Prod ES password |
| `ECE_KIBANA_URL` | for Kibana endpoints | Kibana URL, e.g. `https://kibana:5601` |
| `ECE_KIBANA_TOKEN` | one of | Kibana ApiKey |
| `ECE_KIBANA_USERNAME` | one of | Kibana username |
| `ECE_KIBANA_PASSWORD` | one of | Kibana password |
| `ECE_SKIP_SSL` | no | `true` to disable SSL verification (default: `false`) |
| `LOG_LEVEL` | no | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: `INFO`) |

### API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| **Security Roles** | | |
| `GET` | `/api/v1/roles?target=nonprod` | List all ES security roles |
| `GET` | `/api/v1/roles/{name}` | Get one role |
| `PUT` | `/api/v1/roles/{name}` | Create / update role |
| `DELETE` | `/api/v1/roles/{name}` | Delete role |
| `POST` | `/api/v1/roles/generate` | Generate role + role-mapping templates (no push) |
| `POST` | `/api/v1/roles/provision` | Generate + push to nonprod **and** prod (wraps `role_rm.push_elk`) |
| **Role-Mappings** | | |
| `GET` | `/api/v1/role-mappings?target=nonprod` | List all role-mappings |
| `GET` | `/api/v1/role-mappings/{name}` | Get one |
| `PUT` | `/api/v1/role-mappings/{name}` | Create / update |
| `DELETE` | `/api/v1/role-mappings/{name}` | Delete |
| **Indexes** | | |
| `GET` | `/api/v1/indexes?pattern=*` | List indexes (_cat/indices) |
| `GET` | `/api/v1/indexes/{name}` | Get index settings + mappings |
| `PUT` | `/api/v1/indexes/{name}` | Create / update index |
| `DELETE` | `/api/v1/indexes/{name}` | Delete index |
| `GET/PUT` | `/api/v1/indexes/templates/{name}` | Get / create index template |
| **Logstash Pipelines** | | |
| `GET` | `/api/v1/logstash-pipelines` | List all pipelines |
| `GET` | `/api/v1/logstash-pipelines/{id}` | Get one pipeline |
| `PUT` | `/api/v1/logstash-pipelines/{id}` | Create / update pipeline |
| `DELETE` | `/api/v1/logstash-pipelines/{id}` | Delete pipeline |
| **Kibana Dashboards** | | |
| `GET` | `/api/v1/kibana/dashboards` | List dashboards |
| `GET` | `/api/v1/kibana/dashboards/{id}` | Get dashboard |
| `POST` | `/api/v1/kibana/dashboards` | Create dashboard |
| `PUT` | `/api/v1/kibana/dashboards/{id}` | Update dashboard |
| `DELETE` | `/api/v1/kibana/dashboards/{id}` | Delete dashboard |

> All ES endpoints accept `?target=nonprod` (default) or `?target=prod` to select the cluster.

Interactive docs: `http://localhost:8002/docs`

---

## Service Catalog

The Service Catalog (`/cribl/catalog`) is a live ops dashboard that aggregates the onboarding status of all applications across Cribl and Elasticsearch.

### Features

- **Real-time data** from the onboarding ES index, Cribl route tables, ES role mappings, and ILM tiers — refreshed every 60 seconds (in-memory cache)
- **Search + filter** by APM ID, name, region, status, and ILM tier
- **Stats bar** showing total apps, onboarded, pending, route count, role count
- **Sortable paginated table** with per-column filters
- **Detail drawer** — click any row to see full request info, Cribl routes, ELK roles, entitlement groups, and raw JSON
- **Offboard** (admin only) — removes Cribl routes, deletes ELK role_mappings and roles, marks status as `offboarded`. Dry-run checkbox available.
- **Re-onboard** (admin only) — re-runs Cribl provisioning via `POST /cribl/run`
- **CSV export** of the current filtered view

### Catalog data sources

| Data | Source |
|---|---|
| App list + status | ES onboarding index (latest 500 docs, deduped by APM ID) |
| Route count + destination | Cribl `GET /api/v1/m/{wg}/routes/{table}` per workspace |
| ELK role count | `_security/role_mapping` from all entitlement clusters |
| ILM tier | `/{index}/_ilm/explain` in parallel (ThreadPoolExecutor, 10 workers) |

### Offboard endpoint

```
DELETE /cribl/api/catalog/{apm_id}?dry_run=true|false
```

Actions taken (in order):
1. Fetch and PATCH Cribl route tables — remove all routes matching the APM ID
2. DELETE matching ELK role_mappings (supports BasicAuth **and** ApiKey via `cluster.token`)
3. DELETE ELK roles whose names contain the APM ID
4. ES `_update_by_query` on `apmid.keyword` — sets `status = offboarded`
5. Bust catalog cache (skipped on dry_run)

---

## Template Files

### route_template_azn.json / route_template_azs.json

One file per region. The script fills in `id`, `filter`, `output`, and `name` for each app. Minimum working example:

```json
{
  "pipeline": "passthru",
  "final": false,
  "disabled": false,
  "clones": [],
  "description": "",
  "enableOutputExpression": false
}
```

### blob_dest_template_{region}_{workspace}.json

One file per region x workspace. The script fills in `id`, `name`, `containerName`, and `description` automatically.

---

## App Input Format

### Single app — via CLI flags

```bash
python cribl-pusher.py --appid APP001 --appname "My Application"
```

### Bulk apps — via text file

Create a file with one app per line:

```
# Lines starting with # are comments
APP001, My First Application
APP002, My Second Application
APP003, Another App
```

Format: `appid, appname` (comma-separated). Blank lines and `#` comments are skipped.

---

## Running the Application

### Web UI (recommended)

```bash
python app.py
```

Opens `http://localhost:5000`. All features are available:

| URL | What |
|---|---|
| `/cribl/` | Unified landing page (login required) |
| `/cribl/login` | Login page |
| `/cribl/logout` | Clear session and redirect to login |
| `/cribl/portal` | Onboarding request form (login required) |
| `/cribl/portal/admin/update-status` | Admin status update |
| `/cribl/app` | Cribl Pusher + ELK Roles UI |
| `/cribl/entitlements` | Entitlement Lookup — browse ELK role mappings |
| `/cribl/api/entitlements` | JSON API — entitlement data from all ES clusters |
| `/cribl/catalog` | Service Catalog — live ops dashboard for all onboarded apps |
| `/cribl/api/catalog` | JSON API — catalog data (60s cache) |
| `/cribl/api/catalog/{apm_id}` | `DELETE` — offboard an app (admin only, supports `?dry_run=true`) |
| `/cribl/run` | `POST` — re-onboard trigger from the catalog (admin only) |
| `/cribl/health` | Health check |
| `/cribl/health/es` | Elasticsearch health check |

### CLI — single app

```bash
python cribl-pusher.py \
  --workspace dev \
  --worker-group default \
  --region azn \
  --appid APP001 \
  --appname "My Application" \
  --yes
```

### CLI — bulk file

```bash
python cribl-pusher.py \
  --workspace dev \
  --worker-group default \
  --region azn \
  --from-file \
  --appfile appids.txt \
  --yes
```

### CLI — dry run

```bash
python cribl-pusher.py --workspace dev --worker-group default --region azn --dry-run --from-file
```

---

## Web UI

### Landing Page (/cribl/)

Links to all sections: Onboarding Portal, Cribl Pusher, Entitlement Lookup, and Admin.

### Onboarding Portal (/cribl/portal)

Client-facing form with the following fields:

| Field | Required | Description |
|---|---|---|
| LAN ID | auto | Auto-populated from session |
| Name / Last Name | auto | Auto-populated from session |
| APM ID | yes | Application ID |
| App Name | yes | Application name (single word, underscores allowed) |
| Region | yes | Azure North (azn) or Azure South (azs) |
| Log Destination | yes | Dynatrace and/or ELK |
| Log Type | yes | Application Logs and/or Metrics |
| Entitlement Groups | yes | AD groups for access (tag input) |

Returns a `REQ-YYYYMMDD-XXXXXXXX` Request ID on success.

### Cribl Pusher (/cribl/app)

Two tabs:

**Tab 1 — Cribl Pusher**
- Portal Request ID (optional — auto-updates status on success)
- Workspace, Worker Group(s), Region
- App Input (single or bulk file)
- Options: Dry Run, Skip SSL, Log Level
- Credentials override, Advanced Options

**Tab 2 — ELK Roles + Cribl Routes**
- Portal Request ID (optional — auto-updates status on success)
- App Input (single or bulk)
- ELK Nonprod/Prod URLs + credentials
- Cribl Workspace, Worker Group, Region
- Options: Dry Run, Order, Skip ELK/Cribl

> **Dry Run defaults to ON** in both tabs. Uncheck it to perform actual writes.

### Entitlement Lookup (/cribl/entitlements)

Browse ELK role mappings and entitlement groups across all configured Elasticsearch clusters.

**Features:**
- Global search across all fields (cluster, entitlement, DN, roles)
- Per-column filter inputs for fine-grained filtering
- Cluster dropdown and status (Enabled/Disabled) dropdown filters
- Sortable columns (click column header)
- Pagination with configurable page size (50/100/250/500/All)
- CSV export of filtered results
- Stats bar showing cluster, entitlement, role, and mapping counts
- Error handling for unreachable clusters (displayed inline)

**Configuration:** Add your ES clusters to the `entitlement.clusters` array in `config.json`. See [Entitlement clusters](#entitlement-clusters).

### Admin (/cribl/portal/admin/update-status)

Manual status update form. Requires admin role.

---

## role_rm.py — ELK Roles + Cribl

### Generated ELK templates

Every run saves four files per app to `ops_rm_r_templates_output/`:

| File | Description |
|---|---|
| `roles_{apmid}.json` | Kibana Dev Console format (human review) |
| `role_mappings_{apmid}.json` | Kibana Dev Console format (human review) |
| `roles_{apmid}_pushable.json` | JSON array ready to push via API |
| `role_mappings_{apmid}_pushable.json` | JSON array ready to push via API |

### Basic usage

```bash
python role_rm.py \
  --app_name "My Application" \
  --apmid    "app00001234" \
  --elk-url  "https://elk.company.com:9200" \
  --elk-user elastic \
  --elk-url-prod "https://elk-prod.company.com:9200" \
  --elk-user-prod elastic \
  --workspace dev \
  --dry-run
```

### Generate templates only (no API calls)

```bash
python role_rm.py \
  --app_name "My Application" \
  --apmid    "app00001234" \
  --skip-elk \
  --skip-cribl
```

---

## Docker

### Docker Compose (recommended)

Create a `.env` file in the project root with your Cribl credentials:

```bash
CRIBL_BASE_URL=http://your-cribl:9000
CRIBL_TOKEN=your-token
# or: CRIBL_USERNAME=admin  CRIBL_PASSWORD=changeme
CRIBL_SKIP_SSL=false
```

Then start all services:

```bash
docker compose up -d --build
```

| Service | URL |
|---|---|
| Flask portal | `http://localhost:5000` |
| Cribl service API | `http://localhost:8001` |
| Cribl service docs | `http://localhost:8001/docs` |
| ECE service API | `http://localhost:8002` |
| ECE service docs | `http://localhost:8002/docs` |

`cribl-framework` waits for both `cribl-service` and `ece-service` to pass their health checks before starting.

### Building images individually

```bash
# Flask portal
docker build -t cribl-framework .

# Cribl service (build context must be project root)
docker build -f cribl_service/Dockerfile -t cribl-service .

# ECE service (build context must be project root)
docker build -f ece_service/Dockerfile -t ece-service .
```

---

## Serving via Apache httpd (bastion)

Docker and Apache both run on the bastion host. Docker binds to loopback only.

```
Browser → https://bastion/cribl/app
          Apache ProxyPass → http://127.0.0.1:5000/cribl/app
          Docker container → Flask :5000  (loopback only)
```

| URL | What |
|---|---|
| `https://bastion/cribl/` | Landing page |
| `https://bastion/cribl/portal` | Onboarding Portal |
| `https://bastion/cribl/app` | Cribl Pusher UI |
| `https://bastion/cribl/entitlements` | Entitlement Lookup |
| `https://bastion/cribl/portal/admin/update-status` | Admin panel |

---

## All CLI Flags

### cribl-pusher.py

| Flag | Default | Description |
|---|---|---|
| `--config` | `config.json` | Path to the config file |
| `--cribl-url` | `""` | Cribl base URL override |
| `--workspace` | *(prompts)* | Workspace name |
| `--worker-group` | *(prompts)* | Worker group |
| `--region` | *(prompts)* | Region: `azn` or `azs` |
| `--allow-prod` | false | Skip ALLOW prompt for protected workspaces |
| `--token` | `""` | Bearer token override |
| `--username` | `""` | Username override |
| `--password` | `""` | Password override |
| `--skip-ssl` | false | Disable SSL verification |
| `--dry-run` | false | Preview only — no writes |
| `--yes` | false | Skip confirmation prompt |
| `--appid` | *(prompts)* | Single app ID |
| `--appname` | *(prompts)* | Single app name |
| `--from-file` | false | Load apps from file |
| `--appfile` | `appids.txt` | Path to apps file |
| `--group-id` | `""` | Insert into route group |
| `--create-missing-group` | false | Create group if missing |
| `--group-name` | `""` | Display name for new group |
| `--min-existing-total-routes` | *(config)* | Safety minimum route count |
| `--diff-lines` | *(config)* | Diff context lines |
| `--snapshot-dir` | *(config)* | Snapshot directory |
| `--log-level` | `INFO` | Log verbosity |
| `--log-file` | `""` | Append logs to file |

### role_rm.py

| Flag | Default | Description |
|---|---|---|
| `--app_name` | *(required)* | Application name |
| `--apmid` | *(required)* | App ID |
| `--from-file` | false | Read from file |
| `--appfile` | `appids.txt` | App list file |
| `--elk-url` | *(required unless --skip-elk)* | ELK nonprod URL |
| `--elk-url-prod` | *(required unless --skip-elk)* | ELK prod URL |
| `--elk-user` / `--elk-password` / `--elk-token` | `""` | ELK nonprod credentials |
| `--elk-user-prod` / `--elk-password-prod` / `--elk-token-prod` | `""` | ELK prod credentials |
| `--cribl-url` | `""` | Cribl URL override |
| `--workspace` | *(required unless --skip-cribl)* | Workspace name |
| `--worker-group` | *(prompts)* | Worker group |
| `--region` | `""` | Region: `azn` or `azs` |
| `--allow-prod` | false | Skip ALLOW prompt |
| `--order` | `elk-first` | `elk-first` or `cribl-first` |
| `--skip-elk` | false | Skip ELK side |
| `--skip-cribl` | false | Skip Cribl side |
| `--dry-run` | false | Preview only |
| `--skip-ssl` | false | Disable SSL |
| `--log-level` | `INFO` | Log verbosity |
| `--yes` | false | Skip confirmation |

---

## Logging

All output uses Python's `logging` module.

| Level | What you see |
|---|---|
| `ERROR` | Only errors and fatal messages |
| `WARNING` | Errors + warnings |
| `INFO` | Normal run output — targets, plan, OK/SKIP lines *(default)* |
| `DEBUG` | Everything above + HTTP verb/URL + per-route detail |

---

## Safety Features

| Guard | What it does |
|---|---|
| **Diff preview** | Shows a full unified diff before confirmation |
| **Minimum routes check** | Refuses to PATCH if fewer than `min_existing_total_routes` |
| **No-shrink check** | Refuses to PATCH if new total < current total |
| **Duplicate skip** | Skips apps whose route name or filter already exist |
| **require_allow** | Protected workspaces require `ALLOW` confirmation |
| **Dry run** | Runs full logic but never writes |
| **Rollback snapshot** | Original routes saved before every PATCH |

---

## Rolling Back a Change

Find the snapshot file from the run output:

```
[SNAPSHOT] cribl_snapshots/prod/routes_snapshot_20260327T143022Z.json
```

Restore it:

```bash
curl -k -X PATCH \
  "https://YOUR_CRIBL:9000/api/v1/m/{worker_group}/routes/{routes_table}" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d @cribl_snapshots/prod/routes_snapshot_20260327T143022Z.json
```

---

## Troubleshooting

### `Config file not found: config.json`

```bash
cp config.example.json config.json
```

### `datastream.elk_url is not configured in config.json`

Add the `datastream` block to your `config.json`:

```json
"datastream": {
  "elk_url": "https://localhost:9200",
  "index": "cribl-onboarding-requests",
  "skip_ssl": true,
  "timeout": 30
}
```

### `FileNotFoundError: route_template_azn.json`

The template files must exist in the same folder. See [Template Files](#template-files).

### `[ERR] login failed: 401`

Wrong username/password. Generate a token in Cribl UI under **Settings > API tokens** and set `credentials.token`.

### `SSL: CERTIFICATE_VERIFY_FAILED`

Set `"skip_ssl": true` in config.json or pass `--skip-ssl` at runtime.

### `[SAFETY] Refusing to PATCH: total_before=0 < min=1`

The GET returned an empty route table. Check `base_url`, `worker_group`, and permissions.

### Portal status not updating

1. `admin_secret` is set in `config.json`
2. `datastream.elk_url` points to the correct ELK cluster
3. Portal Request ID was filled in before clicking Run
4. **Dry Run was unchecked**

### Entitlement Lookup shows "No entitlement clusters configured"

Add the `entitlement` block to your `config.json`:

```json
"entitlement": {
  "clusters": [
    { "name": "production", "url": "https://elk-prod:9200", "username": "elastic", "password": "changeme" }
  ],
  "entitlementFilter": "entitlements"
}
```

### Entitlement Lookup shows connection errors for a cluster

1. Verify the cluster URL is reachable from the server
2. Check username/password credentials
3. Ensure the user has permissions to read `/_security/role_mapping`
4. Set `"skip_ssl": true` globally if using self-signed certificates

### Docker container can't reach Cribl/ELK

Use `host.docker.internal` instead of `localhost`:

```json
"base_url": "https://host.docker.internal:9000"
```

### `cribl-service` returns 500 — "CRIBL_BASE_URL env var is not set"

Set `CRIBL_BASE_URL` in your `.env` file or `docker-compose.yml` environment block:

```bash
CRIBL_BASE_URL=http://your-cribl:9000
```

### `cribl-service` returns 502 — "Cribl login failed"

Check `CRIBL_TOKEN` (or `CRIBL_USERNAME` + `CRIBL_PASSWORD`) in the environment. If using self-signed certs set `CRIBL_SKIP_SSL=true`.

### `cribl-framework` fails to start — "cribl_service is unhealthy"

`cribl-framework` depends on both `cribl_service` and `ece_service` being healthy. Check service logs:

```bash
docker compose logs cribl_service
docker compose logs ece_service
```

### Service Catalog shows empty or stale data

- The catalog caches for 60 seconds. Click **Refresh** to force a rebuild.
- Verify `datastream.elk_url` is reachable (check `/health/es`).
- Ensure Cribl credentials are valid — the catalog fetches routes for every workspace.
- ILM tier checks use the same ES credentials as the datastream config.

### Offboard returns `updated: 0` on the ES status update

The ES index may have `apmid` mapped as `text` without a `keyword` sub-field. Apply the index template from `elk-index-template.json` (which maps `apmid` as `keyword`) or re-index existing documents:

```bash
curl -k -X PUT "https://YOUR_ELK:9200/_index_template/cribl-onboarding-requests" \
  -H "Content-Type: application/json" \
  -d @elk-index-template.json
```
