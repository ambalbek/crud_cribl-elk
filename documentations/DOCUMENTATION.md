# Untracked AppId Detector for Cribl Stream — Technical Documentation

## Table of Contents

1. [Overview](#overview)
2. [Problem Statement](#problem-statement)
3. [Architecture](#architecture)
4. [Components](#components)
5. [Authentication](#authentication)
6. [Data Flow](#data-flow)
7. [Configuration](#configuration)
8. [Operational Modes](#operational-modes)
9. [Matching Engine](#matching-engine)
10. [Output Formats](#output-formats)
11. [Deduplication & Diff Logic](#deduplication--diff-logic)
12. [Error Handling & Fault Tolerance](#error-handling--fault-tolerance)
13. [Security Considerations](#security-considerations)
14. [Deployment & Operations](#deployment--operations)
15. [Appendix](#appendix)

---

## 1. Overview

**find_default_appids.py** is a read-only audit tool for Cribl Stream that identifies application IDs (appIds) actively falling through to a default Azure Blob storage destination because they lack dedicated routes or destinations.

| Attribute | Value |
|-----------|-------|
| Language | Python 3.9+ |
| Dependencies | `requests` |
| Cribl API Version | REST API v1 |
| Design Principle | Read-only — no config is created, modified, or persisted |

### What It Does

- Captures live events from Cribl Stream worker groups
- Identifies appIds routed to the default Azure Blob destination
- Compares captured appIds against existing dedicated destinations
- Filters out already-known containers via lookup tables
- **Audits lookup appIds hitting default** — checks whether they have routes and destinations configured in Cribl, and flags misconfigurations (missing route, missing destination, or both)
- Tracks new unmatched appIds across runs via CSV diffing
- Outputs results to CSV, JSON, and/or Elasticsearch

### What It Does NOT Do

- Modify any Cribl Stream configuration
- Create, update, or delete routes or destinations
- Persist any data within Cribl Stream itself

---

## 2. Problem Statement

In a Cribl Stream deployment, events from various applications (identified by `apmId`) are routed to Azure Blob storage destinations. A **default** catch-all destination exists for events that don't match any specific route. Over time, new applications onboard and begin sending data — but if no dedicated destination is configured, their events silently land in the default blob container.

**Impact:**
- Data governance gaps — sensitive data may land in an unmonitored default container
- Cost inefficiency — default container may lack optimized retention/tiering policies
- Operational blind spots — teams don't know their data isn't reaching the intended destination

**This tool solves the problem** by continuously auditing live traffic, surfacing appIds that have no dedicated destination, and flagging appIds that have Azure containers provisioned but are missing the Cribl route and/or destination needed to actually use them.

---

## 3. Architecture

### Package Structure

```
cribl_audit/
  __init__.py       Public API exports
  __main__.py       python -m cribl_audit entry point
  cli.py            Argparse, config merging, main()
  config.py         JSON config + .env file loading
  constants.py      Timeouts, exit codes, URLs
  exceptions.py     CriblAPIError, AuthenticationError
  http.py           Session factory with retry logic
  auth.py           CriblAuth (OAuth2, leader login, static token)
  client.py         CriblClient (outputs, routes, capture)
  matching.py       match_appid_to_dest + route/dest audit
  lookup.py         Lookup table loading + CSV diff
  output.py         CSV, JSON, lookup_status writers + tables
  elasticsearch.py  ElasticsearchClient (bulk indexing)
  analysis.py       run_inspect, run_dry_run, run_analysis
```

### High-Level Architecture

```
+-------------------------------------------------------------------+
|                    cli.py  (CLI / Config / Env)                    |
+-------------------------------------------------------------------+
                              |
                    +---------v----------+
                    |   Configuration    |
                    |   Resolver         |
                    |  (CLI > JSON >     |
                    |   ENV > defaults)  |
                    +---------+----------+
                              |
                    +---------v----------+
                    |   CriblAuth        |
                    |  (OAuth2 / Login / |
                    |   Static Token)    |
                    +---------+----------+
                              |
                    +---------v----------+
                    |   CriblClient      |
                    |  (REST API wrapper)|
                    +---------+----------+
                              |
              +---------------+----------------+
              |               |                |
        +-----v-----+  +-----v-----+  +-------v-----+
        |  Worker    |  |  Worker    |  |  Worker      |
        |  Group 1   |  |  Group 2   |  |  Group N     |
        | (parallel) |  | (parallel) |  |  (parallel)  |
        +-----+------+  +-----+------+  +------+------+
              |               |                |
              +-------+-------+-------+--------+
                      |
            +---------v----------+
            |   Matching Engine  |
            |  (exact/contains/  |
            |   partition)       |
            +---------+----------+
                      |
            +---------v----------+
            |  Dedup & Diff      |
            |  (lookup table,    |
            |   previous CSV)    |
            +---------+----------+
                      |
            +---------v----------+
            | Lookup Route/Dest  |
            | Audit              |
            | (GET /routes,      |
            |  check config)     |
            +---------+----------+
                      |
        +-------------+-------------+
        |             |             |
   +----v----+  +----v----+  +-----v------+
   |   CSV   |  |  JSON   |  | Elastic-   |
   |  Output |  |  Output |  | search     |
   +---------+  +---------+  +------------+
```

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Modular package (14 files) | Each module is 20-200 lines — easy to read, test, and extend independently |
| Read-only API usage | Safety — tool can be run by anyone without risk of config corruption |
| Thread-based parallelism | Worker groups are I/O-bound (network calls) — threads are sufficient |
| Three-tier config resolution | Flexibility — supports CI/CD (env vars), interactive use (config file), and ad-hoc overrides (CLI) |
| CSV diff across runs | Enables incremental alerting — only new untracked appIds trigger notifications |
| Lookup route/dest audit | Catches appIds with containers that are misconfigured in Cribl (missing route and/or destination) |

---

## 4. Components

### 4.1 CriblAuth

**Purpose:** Thread-safe authentication manager with automatic token refresh.

| Feature | Detail |
|---------|--------|
| Auth methods | OAuth2 (Cribl Cloud), Leader login (self-managed), Static token |
| Token caching | Thread-safe in-memory cache with TTL |
| Auto-refresh | Token refreshed before each capture round |
| Cloud endpoint | `https://login.cribl.cloud/oauth/token` |

**Auth Priority Order:**
1. `client_id` + `client_secret` → OAuth2 client credentials flow
2. `username` + `password` → Leader `/api/v1/auth/login` endpoint
3. `token` → Used as-is (Bearer header)

### 4.2 CriblClient

**Purpose:** Read-only wrapper around the Cribl Stream REST API.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/m/{group}/system/outputs` | GET | List all configured destinations |
| `/api/v1/m/{group}/system/capture` | POST | Live event capture (transient, no persistence) |
| `/api/v1/m/{group}/routes` | GET | List all configured routes (for lookup audit) |

**Timeouts:**
- Connect: 10 seconds
- Read: 30 seconds (+ dynamic padding for capture duration)

**Retry Logic:**
- 3 attempts with exponential backoff
- Retries on HTTP 429 (rate limited) and 5xx (server error)

### 4.3 ElasticsearchClient

**Purpose:** Bulk-indexes untracked appId results into Elasticsearch for dashboarding and alerting.

| Feature | Detail |
|---------|--------|
| Bulk API | `POST /{index}/_bulk` |
| Auth options | API key (recommended) or username/password |
| Error handling | Per-document error reporting, diagnostic logging |

**Document Schema:**

| Field | Type | Description |
|-------|------|-------------|
| `@timestamp` | ISO 8601 | When the analysis ran |
| `group` | string | Cribl worker group name |
| `apmId` | string | The application ID |
| `appName` | string | Application name (if available) |
| `outputId` | string | Cribl output the event was heading to |
| `matched_destination` | string | Destination ID or `DEFAULT` |
| `is_unmatched` | boolean | True if no dedicated destination exists |
| `is_new` | boolean | True if not seen in previous runs |
| `event_count` | integer | Number of events seen for this appId |
| `total_events_captured` | integer | Total events in the capture round |

### 4.4 Matching Engine

**Purpose:** Determines whether a captured appId has a dedicated Azure Blob destination.

Three modes:
- **exact** — Case-insensitive equality: `containerName == appId`
- **contains** — Substring: `appId in containerName`
- **partition** — Exact match OR `appId` appears in `partitionExpr`

If no match is found, the appId is marked as `DEFAULT` (unmatched).

### 4.5 Lookup & Diff System

**Purpose:** Prevents duplicate reporting and excludes already-provisioned containers.

| Function | Input | Effect |
|----------|-------|--------|
| `load_lookup_appids()` | `APP_*.json` file | Excludes appIds listed in `azure_storage_account_containers` |
| `load_previous_unmatched()` | Previous CSV file | Excludes appIds already reported in prior runs |
| `find_latest_csv()` | Output directory | Auto-detects most recent CSV for comparison |

### 4.6 Lookup Route/Destination Audit

**Purpose:** For appIds that have Azure containers (per lookup table) but are still hitting the default destination, verifies whether routes and destinations are actually configured in Cribl.

**Problem this solves:** An appId can have an Azure container provisioned (listed in the lookup table) yet still land in the default output because:
- The Cribl destination pointing to that container was never created
- The Cribl route to direct traffic to that destination was never created
- Both are missing

**How matching works:**

Destination is matched if any of these are true (case-insensitive):
1. `containerName` matches the appId (via configured match mode: exact/contains/partition)
2. Destination `id` contains the appId (e.g. `azure_blob:prod-my-app` matches `my-app`)
3. Destination `name` contains the appId

Route is matched if any of these are true (case-insensitive):
1. Route `name` contains the appId (e.g. `prod-my-app-route` matches `my-app`)
2. Route `id` contains the appId
3. Route `filter` expression contains the appId
4. Route `output` points to the appId's matched destination

**Status categories:**

| Status | Meaning | Action Needed |
|--------|---------|---------------|
| `CONFIGURED` | Has both route and destination | Investigate why traffic still hits default (disabled route? filter mismatch?) |
| `MISSING ROUTE` | Destination exists but no route | Create a route to direct traffic to the destination |
| `MISSING DESTINATION` | Route exists but no destination | Create an azure_blob destination for the container |
| `MISSING BOTH` | Neither route nor destination | Create both route and destination |

**Output:**
- Console table with `<<<` markers for misconfigured appIds
- Separate CSV file: `*_lookup_status.csv` with columns: `apmId`, `has_destination`, `destination_id`, `has_route`, `route_id`, `route_output`, `status`

---

## 5. Authentication

### OAuth2 (Cribl Cloud)

```
Client                          Cribl Cloud IdP
  |                                   |
  |  POST /oauth/token                |
  |  grant_type=client_credentials    |
  |  client_id=XXX                    |
  |  client_secret=XXX               |
  |  audience=https://api.cribl.cloud |
  |---------------------------------->|
  |                                   |
  |  { access_token, expires_in }     |
  |<----------------------------------|
  |                                   |
  |  GET /api/v1/m/{group}/...        |
  |  Authorization: Bearer <token>    |
  |---------------------------------->|
```

### Leader Login (Self-Managed)

```
Client                          Cribl Leader
  |                                   |
  |  POST /api/v1/auth/login          |
  |  { username, password }           |
  |---------------------------------->|
  |                                   |
  |  { token }                        |
  |<----------------------------------|
  |                                   |
  |  GET /api/v1/m/{group}/...        |
  |  Authorization: Bearer <token>    |
  |---------------------------------->|
```

### Static Token

No authentication exchange — the pre-existing token is sent directly as `Authorization: Bearer <token>`.

---

## 6. Data Flow

### Per Worker Group (Parallel)

```
For each round (1..N):
  1. Refresh auth token (if needed)
  2. POST /api/v1/m/{group}/system/capture
     - Filter: events heading to default output
     - Duration: --seconds (default 30)
     - Max events: --max-events (default 5000)
     - Level: --level (default 3, before destination)
  3. Parse NDJSON response stream
  4. Extract apmId field from each event
  5. Count occurrences per apmId
  6. Wait --interval seconds before next round

After all rounds:
  7. GET /api/v1/m/{group}/system/outputs
  8. Filter to azure_blob type destinations
  9. Match each captured apmId against destinations
  10. Mark unmatched appIds as DEFAULT
```

### Post-Processing (Sequential)

```
  11. Merge results from all worker groups
  12. Load lookup table → exclude known containers
  13. Identify lookup appIds still hitting default
      a. GET /api/v1/m/{group}/routes
      b. For each lookup appId hitting default:
         - Check if destination exists (containerName, dest id/name)
         - Check if route exists (route name/id, filter, output)
         - Categorize: CONFIGURED / MISSING ROUTE / MISSING DEST / MISSING BOTH
      c. Print [ALERT] table + write *_lookup_status.csv
  14. Load previous CSV → exclude already-reported appIds
  15. Output NEW unmatched appIds to CSV/JSON/Elasticsearch
```

---

## 7. Configuration

### Three-Tier Priority

```
CLI arguments  >  config.json  >  Environment variables  >  Hardcoded defaults
```

### Config File Sections

| Section | Keys | Purpose |
|---------|------|---------|
| `auth` | `cribl_url`, `username`, `password`, `client_id`, `client_secret`, `token` | Cribl authentication |
| `capture` | `groups`, `seconds`, `max_events`, `level`, `rounds`, `interval`, `appid_field` | Event capture parameters |
| `matching` | `mode`, `default_output` | How appIds are matched to destinations |
| `output` | `format`, `append`, `lookup` | Output file settings |
| `elasticsearch` | `url`, `index`, `api_key`, `username`, `password` | ES integration |
| `logging` | `log_file`, `verbose` | Log configuration |
| `connection` | `verify_ssl` | SSL/TLS settings |

### Environment Variables

| Variable | Maps To |
|----------|---------|
| `CRIBL_URL` | `auth.cribl_url` |
| `CRIBL_USERNAME` | `auth.username` |
| `CRIBL_PASSWORD` | `auth.password` |
| `CRIBL_CLIENT_ID` | `auth.client_id` |
| `CRIBL_CLIENT_SECRET` | `auth.client_secret` |
| `CRIBL_TOKEN` | `auth.token` |
| `ES_URL` | `elasticsearch.url` |
| `ES_INDEX` | `elasticsearch.index` |
| `ES_API_KEY` | `elasticsearch.api_key` |
| `ES_USERNAME` | `elasticsearch.username` |
| `ES_PASSWORD` | `elasticsearch.password` |

---

## 8. Operational Modes

### Inspect Mode (`--inspect`)

Non-destructive discovery mode:
- Shows all configured destinations
- Captures sample events and displays field names
- Suggests filter expressions for routing field detection
- Use this first to understand your Cribl topology

### Dry Run (`--dry-run`)

Validation mode:
- Authenticates and validates connectivity
- Confirms worker group existence
- Shows ES configuration
- Displays the run plan (groups, rounds, duration)
- Estimates execution time
- **No events are captured**

### Full Analysis (default)

Production mode:
- Multi-round capture with configurable intervals
- Parallel group processing via ThreadPoolExecutor
- Graceful Ctrl+C handling (saves partial results)
- Diff against previous CSV to surface only new findings
- Elasticsearch indexing for dashboarding

---

## 9. Matching Engine

### How Matching Works

For each captured appId, the engine checks all `azure_blob` type destinations:

```
For each azure_blob destination:
    Extract containerName from destination config

    if mode == "exact":
        match = (containerName.lower() == appId.lower())

    elif mode == "contains":
        match = (appId.lower() in containerName.lower())

    elif mode == "partition":
        match = (containerName.lower() == appId.lower())
                OR (appId in destination.partitionExpr)

    if match:
        return destination_id  # appId is tracked

return "DEFAULT"  # appId is untracked
```

### Filtering Pipeline

An appId appears in the final output only if **all** conditions are true:

1. It was captured in live events heading to the default output
2. It does NOT match any existing `azure_blob` destination (via match mode)
3. It does NOT exist in the lookup file (`azure_storage_account_containers`)
4. It does NOT exist in a previous CSV run (unless this is the first run)

---

## 10. Output Formats

### CSV

- Filename: `appids_without_destination_YYYYMMDD_HHMMSS.csv`
- Columns: `apmId`, `appName`, `outputId`, `matched_destination`, `event_count`
- Append mode available for incremental runs (deduplicates by apmId)

### JSON

- Same data as CSV plus metadata:
  - `timestamp` — ISO 8601 run time
  - `group` — worker group name
  - `total_events_captured` — total events in capture
  - `unmatched_count` — number of untracked appIds

### Elasticsearch

- Each appId indexed as a separate document (see schema in Section 4.3)
- Bulk API for efficient indexing
- Supports API key or basic auth

### Lookup Status CSV

When lookup appIds are detected hitting the default destination, an additional CSV is written:

- Filename: `*_lookup_status.csv`
- Columns:

| Column | Description |
|--------|-------------|
| `apmId` | The application ID from the lookup table |
| `has_destination` | Whether a matching Cribl destination exists (`True`/`False`) |
| `destination_id` | The matched destination ID, or `NONE` |
| `has_route` | Whether a matching Cribl route exists (`True`/`False`) |
| `route_id` | The matched route ID, or `NONE` |
| `route_output` | The route's configured output destination, or `NONE` |
| `status` | One of: `CONFIGURED`, `MISSING ROUTE`, `MISSING DESTINATION`, `MISSING BOTH` |

---

## 11. Deduplication & Diff Logic

### Lookup Table Exclusion

The `--lookup` flag points to a JSON file listing containers that are already provisioned (even if not yet active in Cribl). AppIds matching these containers are excluded from output.

```json
{
  "azure_storage_account_containers": ["app-one", "app-two"]
}
```

### CSV Diff

When a previous CSV exists (auto-detected or specified via `--diff-csv`):
- AppIds already in the previous CSV are excluded from the new output
- Only **newly discovered** unmatched appIds are reported
- This enables incremental alerting workflows

### Lookup Route/Destination Audit

When lookup appIds are excluded from the main output (they have containers), they are **not silently ignored**. If any of these appIds are still hitting the default destination, the tool:

1. Fetches all routes via `GET /api/v1/m/{group}/routes`
2. Checks each lookup appId for a matching destination (by containerName, dest ID, or dest name)
3. Checks each lookup appId for a matching route (by route name, route ID, filter, or output)
4. Reports the status as an `[ALERT]` table and writes `*_lookup_status.csv`

This catches the scenario where an Azure container is provisioned but the Cribl configuration to actually route data there is incomplete.

### Append Mode

With `--append`, new results are appended to an existing CSV. Existing rows are read first to prevent duplicates.

---

## 12. Error Handling & Fault Tolerance

### HTTP Retry Logic

| Condition | Behavior |
|-----------|----------|
| HTTP 429 (Rate Limited) | Retry with exponential backoff (3 attempts) |
| HTTP 5xx (Server Error) | Retry with exponential backoff (3 attempts) |
| Connection timeout | Fail after 10 seconds |
| Read timeout | Fail after 30 seconds (+ capture duration padding) |

### Graceful Shutdown

- Ctrl+C triggers `KeyboardInterrupt` handler
- Partial results from completed rounds are saved
- Exit code 130 indicates interrupted run

### Partial Failure

- If some worker groups succeed and others fail, results from successful groups are still saved
- Exit code 2 indicates partial success
- Per-group errors are logged with full stack traces

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All groups succeeded |
| 1 | Fatal error (auth failure, no connectivity, no events captured) |
| 2 | Partial success (some groups failed) |
| 130 | User interrupted (Ctrl+C) — partial results saved |

---

## 13. Security Considerations

### Credential Protection

- `.gitignore` blocks `config.json`, `.env*`, and output files
- Environment file world-readable warning (recommends `chmod 600`)
- Three auth methods avoid hardcoding secrets in scripts
- SSL verification enabled by default (`--no-verify-ssl` to override)

### Read-Only Design

- Only uses `GET` for listing destinations and routes
- `POST /system/capture` is transient — no data is written to Cribl
- No mutations to Cribl configuration, routes, or pipelines

### Network Security

- TLS/SSL verification enabled by default
- Configurable via `connection.verify_ssl` or `--no-verify-ssl`
- Warning logged when SSL verification is disabled

---

## 14. Deployment & Operations

### Prerequisites

- Python 3.9+
- `pip install requests`
- Network access to Cribl leader/cloud and (optionally) Elasticsearch

### Installation

```bash
# Copy the package and config template
cp -r cribl_audit/ /opt/cribl-audit/cribl_audit/
cp config.example.json /opt/cribl-audit/config.json
chmod 600 /opt/cribl-audit/config.json

# Install dependency
pip install requests
```

### Offline Installation (Air-Gapped / No Internet)

If the target Linux host has no internet access, download wheel files from a
connected machine first, then transfer them.

**Step 1 — Download wheels (on a machine with internet):**

```bash
# For Linux x86_64, Python 3.11
pip download requests urllib3 azure-storage-blob \
  --dest ./wheels \
  --platform manylinux2014_x86_64 \
  --python-version 311 \
  --only-binary=:all:
```

Adjust flags for your target environment:

| Flag | Example values |
|------|----------------|
| `--platform` | `manylinux2014_x86_64`, `manylinux2014_aarch64` |
| `--python-version` | `39` (3.9), `310` (3.10), `311` (3.11), `312` (3.12) |

If some packages fail with *"no matching distribution"*, try adding multiple
platform tags:

```bash
--platform manylinux2014_x86_64 \
--platform manylinux_2_17_x86_64 \
--platform linux_x86_64
```

**Step 2 — Transfer the `wheels/` directory** to the offline host (e.g., via
`scp`, USB drive, or shared mount).

**Step 3 — Install from local wheels on the offline host:**

```bash
pip install --no-index --find-links=./wheels requests urllib3 azure-storage-blob
```

> `--no-index` tells pip to skip PyPI entirely and only use the local directory.

### Recommended Workflow

```bash
# Step 1: Validate configuration
python -m cribl_audit --config config.json --dry-run

# Step 2: Discover field names and topology
python -m cribl_audit --config config.json --inspect

# Step 3: Run analysis
python -m cribl_audit --config config.json

# Step 4: Schedule recurring runs (e.g., daily via cron)
# 0 6 * * * cd /opt/cribl-audit && /usr/bin/python3 -m cribl_audit --config config.json --append
```

### Monitoring

- Log file: configurable via `--log-file` or `logging.log_file`
- Elasticsearch index: build Kibana dashboards on `cribl-untracked-appids`
- Exit codes: use in CI/CD or monitoring scripts to detect failures

---

## 15. Appendix

### Capture Levels

| Level | Stage | Use Case |
|-------|-------|----------|
| 0 | Before pre-processing pipeline | See raw ingest data |
| 1 | Before routes | See data before routing decisions |
| 2 | Before post-processing pipeline | See data after routing |
| 3 | Before destination (default) | See final data heading to output |

### Glossary

| Term | Definition |
|------|------------|
| **appId / apmId** | Application identifier field in events, used to route data to the correct destination |
| **Worker Group** | A set of Cribl Stream worker nodes processing data in parallel |
| **Default Destination** | The catch-all Azure Blob output that receives events not matching any specific route |
| **Lookup Table** | A JSON file listing containers that are already provisioned |
| **Capture** | Cribl's live event sampling feature — transient, no data is persisted |
| **NDJSON** | Newline-delimited JSON — the format used by Cribl's capture API response |

### API Endpoints Used

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /oauth/token` | POST | Cribl Cloud OAuth2 token exchange |
| `POST /api/v1/auth/login` | POST | Self-managed leader authentication |
| `GET /api/v1/m/{group}/system/outputs` | GET | List all configured destinations |
| `GET /api/v1/m/{group}/routes` | GET | List all configured routes |
| `POST /api/v1/m/{group}/system/capture` | POST | Live event capture (transient) |
