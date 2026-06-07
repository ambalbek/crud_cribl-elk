# Jira Tickets — Untracked AppId Detector for Cribl Stream

---

## EPIC: CRIBL-100 — Untracked AppId Detection & Routing Gap Audit

**Type:** Epic
**Priority:** High
**Labels:** `cribl`, `data-governance`, `azure-blob`, `observability`
**Components:** Data Pipeline, Platform Engineering

### Summary

Build a read-only audit tool that identifies application IDs (appIds) actively falling through to the default Azure Blob storage destination in Cribl Stream because they lack dedicated routes/destinations. The tool captures live traffic, matches appIds against configured destinations, and reports untracked appIds via CSV, JSON, and Elasticsearch.

### Business Value

- **Data Governance** — Prevent sensitive data from landing in unmonitored default containers
- **Cost Optimization** — Ensure all data flows through properly tiered storage destinations
- **Operational Visibility** — Proactively detect new applications missing dedicated routing
- **Compliance** — Audit trail of routing gaps with timestamps and event counts

### Acceptance Criteria

- [ ] Tool captures live events from Cribl Stream worker groups
- [ ] Identifies appIds with no dedicated Azure Blob destination
- [ ] Supports multiple authentication methods (OAuth2, leader login, static token)
- [ ] Outputs results to CSV, JSON, and Elasticsearch
- [ ] Tracks new vs. previously-seen unmatched appIds across runs
- [ ] Read-only — no Cribl configuration is modified
- [ ] Comprehensive documentation, flowcharts, and operational runbooks

---

## Story: CRIBL-101 — Authentication Module

**Type:** Story
**Priority:** High
**Epic:** CRIBL-100
**Story Points:** 5
**Labels:** `auth`, `security`

### Summary

Implement a thread-safe authentication module (`CriblAuth`) that supports three authentication methods for Cribl Stream: OAuth2 client credentials (Cribl Cloud), leader login (self-managed), and static bearer tokens.

### Description

The authentication module must handle token lifecycle management including caching, automatic refresh, and thread safety for parallel worker group processing.

**Auth methods (checked in priority order):**
1. **OAuth2** — `client_id` + `client_secret` → `POST https://login.cribl.cloud/oauth/token`
2. **Leader Login** — `username` + `password` → `POST /api/v1/auth/login`
3. **Static Token** — Bearer token used directly

### Acceptance Criteria

- [ ] OAuth2 client credentials flow with Cribl Cloud IdP
- [ ] Self-managed leader username/password login
- [ ] Static bearer token passthrough
- [ ] Thread-safe token caching with TTL-based expiry
- [ ] Automatic token refresh before each capture round
- [ ] Clear error messages for auth failures
- [ ] Credentials sourced from config file, environment variables, or CLI args

### Technical Notes

- Cloud OAuth endpoint: `https://login.cribl.cloud/oauth/token`
- Audience: `https://api.cribl.cloud`
- Use `threading.Lock` for thread-safe token access

---

## Story: CRIBL-102 — Configuration Resolution System

**Type:** Story
**Priority:** High
**Epic:** CRIBL-100
**Story Points:** 3
**Labels:** `config`, `cli`

### Summary

Implement a three-tier configuration resolution system: CLI arguments > config.json > environment variables > hardcoded defaults.

### Description

The tool needs flexible configuration to support interactive use (config file), CI/CD pipelines (env vars), and ad-hoc overrides (CLI args). All configuration sections (auth, capture, matching, output, elasticsearch, logging, connection) must follow this priority chain.

### Acceptance Criteria

- [ ] CLI arguments override all other sources
- [ ] `config.json` provides structured configuration with all sections
- [ ] Environment variables serve as fallback (`CRIBL_URL`, `CRIBL_USERNAME`, etc.)
- [ ] Sensible hardcoded defaults for all non-required fields
- [ ] `.env` file loading with security warnings for world-readable files
- [ ] `config.example.json` template with all available options
- [ ] `.env.example` template with all supported environment variables

### Technical Notes

- Use `argparse` for CLI argument parsing
- Config sections: `auth`, `capture`, `matching`, `output`, `elasticsearch`, `logging`, `connection`
- `.env` loader: no external dependency (custom parser)

---

## Story: CRIBL-103 — Cribl API Client & Event Capture

**Type:** Story
**Priority:** High
**Epic:** CRIBL-100
**Story Points:** 8
**Labels:** `api`, `capture`, `core`

### Summary

Implement the read-only Cribl API client (`CriblClient`) with support for listing destinations and capturing live events from worker groups.

### Description

The client wraps two Cribl REST API endpoints:
1. `GET /api/v1/m/{group}/system/outputs` — List configured destinations
2. `POST /api/v1/m/{group}/system/capture` — Live event capture (transient)

Event capture must support configurable duration, max events, capture level, filter expressions, and multiple rounds with intervals.

### Acceptance Criteria

- [ ] List all configured destinations for a worker group
- [ ] Capture live events with configurable parameters (duration, max events, level, filter)
- [ ] Parse NDJSON response stream
- [ ] Extract `apmId` field (configurable field path) from captured events
- [ ] Count event occurrences per appId
- [ ] Support multiple capture rounds with configurable interval between rounds
- [ ] HTTP retry logic: 3 attempts with exponential backoff for 429/5xx
- [ ] Configurable timeouts: 10s connect, 30s read (+ dynamic padding for capture duration)
- [ ] Capture levels 0-3 supported

### Technical Notes

- Use `requests` with `HTTPAdapter` and `Retry` for resilience
- NDJSON parsing: line-by-line JSON decode
- Capture level 3 (before destination) is the default

---

## Story: CRIBL-104 — Matching Engine

**Type:** Story
**Priority:** High
**Epic:** CRIBL-100
**Story Points:** 5
**Labels:** `matching`, `core`

### Summary

Implement the matching engine that determines whether a captured appId has a dedicated Azure Blob destination or is falling through to the default.

### Description

Three match modes determine how appIds are compared against `azure_blob` destination configurations:
- **exact** — Case-insensitive string equality (`containerName == appId`)
- **contains** — Substring match (`appId` appears in `containerName`)
- **partition** — Exact match OR `appId` appears in `partitionExpr`

AppIds with no match are marked as `DEFAULT` (untracked).

### Acceptance Criteria

- [ ] `exact` mode: case-insensitive equality matching
- [ ] `contains` mode: substring matching
- [ ] `partition` mode: exact match OR partition expression matching
- [ ] Auto-detection of the default output ID from configured destinations
- [ ] Filter destinations to `azure_blob` type only
- [ ] Each appId mapped to either a specific destination ID or `DEFAULT`
- [ ] Match mode configurable via CLI, config file, or env var

### Technical Notes

- Default output auto-detection: look for the destination with the catch-all pattern
- Case-insensitive matching throughout

---

## Story: CRIBL-105 — Parallel Worker Group Processing

**Type:** Story
**Priority:** Medium
**Epic:** CRIBL-100
**Story Points:** 5
**Labels:** `parallelism`, `performance`

### Summary

Implement parallel processing of multiple Cribl worker groups using `ThreadPoolExecutor`, with thread-safe result aggregation and per-group error handling.

### Description

Multiple worker groups should be processed concurrently to reduce total execution time. Each group runs its own capture rounds independently, and results are merged after all groups complete (or fail).

### Acceptance Criteria

- [ ] Multiple worker groups processed in parallel via `ThreadPoolExecutor`
- [ ] Thread-safe output buffering to prevent interleaved console output
- [ ] Per-group error tracking — one group failing doesn't abort others
- [ ] Results from all successful groups merged into a single dataset
- [ ] Partial success mode: exit code 2 if some groups fail, others succeed
- [ ] Graceful Ctrl+C handling: save partial results from completed rounds
- [ ] Exit code 130 for interrupted runs

### Technical Notes

- Use `concurrent.futures.ThreadPoolExecutor` with `as_completed()`
- Thread-safe buffering via `threading.Lock` or `io.StringIO` per group
- `KeyboardInterrupt` handler saves partial results before exit

---

## Story: CRIBL-106 — Deduplication & Diff System

**Type:** Story
**Priority:** Medium
**Epic:** CRIBL-100
**Story Points:** 5
**Labels:** `dedup`, `diff`, `lookup`

### Summary

Implement the lookup table exclusion and CSV diff system to prevent duplicate reporting and enable incremental alerting.

### Description

Three deduplication mechanisms ensure only **new** untracked appIds are reported:
1. **Lookup table** — Exclude appIds with already-provisioned Azure containers (from `APP_*.json`)
2. **CSV diff** — Exclude appIds already reported in previous runs
3. **Append mode** — Deduplicate when appending to an existing CSV file

### Acceptance Criteria

- [ ] Load lookup JSON (`azure_storage_account_containers` array)
- [ ] Case-insensitive exclusion of lookup appIds
- [ ] Auto-detect latest previous CSV file for diffing
- [ ] Manual previous CSV specification via `--diff-csv`
- [ ] Exclude previously-reported appIds from new output
- [ ] Append mode: read existing CSV rows before writing to prevent duplicates
- [ ] `is_new` flag in Elasticsearch documents for new vs. previously-seen
- [ ] `find_latest_csv()` function to locate most recent output file

### Technical Notes

- Lookup file only reads `azure_storage_account_containers` key
- CSV parsing via `csv.DictReader`
- Glob pattern `appids_without_destination_*.csv` for auto-detection

---

## Story: CRIBL-107 — Output Handlers (CSV, JSON, Elasticsearch)

**Type:** Story
**Priority:** High
**Epic:** CRIBL-100
**Story Points:** 5
**Labels:** `output`, `elasticsearch`, `csv`, `json`

### Summary

Implement output handlers for CSV, JSON, and Elasticsearch formats, including console table display.

### Description

Results must be output in one or more formats:
- **Console** — Formatted table with `<<<` markers for unmatched appIds
- **CSV** — Timestamped file with appId details and event counts
- **JSON** — Structured output with metadata (timestamp, group, totals)
- **Elasticsearch** — Bulk-indexed documents with `is_unmatched` and `is_new` flags

### Acceptance Criteria

- [ ] Console table with column alignment and `<<<` markers for DEFAULT appIds
- [ ] CSV output: timestamped filename, columns: apmId, appName, outputId, matched_destination, event_count
- [ ] JSON output: same data + metadata (timestamp, group, totals)
- [ ] Elasticsearch bulk indexing via `POST /{index}/_bulk`
- [ ] ES auth: API key (recommended) or username/password
- [ ] Per-document error reporting for ES bulk failures
- [ ] `--format` flag: `csv`, `json`, or `both`
- [ ] `--output` flag for custom output file path

### Technical Notes

- ES bulk API uses NDJSON format (action + document pairs)
- `@timestamp` field in ISO 8601 format
- CSV writer: `csv.DictWriter` with consistent column ordering

---

## Story: CRIBL-108 — Inspect & Dry-Run Modes

**Type:** Story
**Priority:** Medium
**Epic:** CRIBL-100
**Story Points:** 3
**Labels:** `ux`, `discovery`

### Summary

Implement `--inspect` (discovery) and `--dry-run` (validation) modes for safe pre-flight checks.

### Description

**Inspect mode** (`--inspect`):
- Lists all configured destinations
- Captures sample events and displays available fields
- Suggests filter expressions for routing field detection
- Helps users understand their Cribl topology before running analysis

**Dry-run mode** (`--dry-run`):
- Validates auth credentials and connectivity
- Confirms worker group existence
- Displays run plan (groups, rounds, duration, ES config)
- Estimates total execution time
- No events are captured

### Acceptance Criteria

- [ ] `--inspect`: list destinations with type and container info
- [ ] `--inspect`: capture sample events and display field names
- [ ] `--inspect`: suggest filter expressions
- [ ] `--dry-run`: validate auth + connectivity
- [ ] `--dry-run`: confirm group existence
- [ ] `--dry-run`: display full run plan with estimated duration
- [ ] `--dry-run`: show ES configuration summary
- [ ] Both modes exit cleanly with code 0

---

## Story: CRIBL-109 — Logging & Observability

**Type:** Story
**Priority:** Low
**Epic:** CRIBL-100
**Story Points:** 2
**Labels:** `logging`, `observability`

### Summary

Implement configurable logging with file and stderr output, verbose mode, and structured log formatting.

### Description

The tool should provide clear operational logging at two levels:
- **INFO** (default): High-level progress, results summary, warnings
- **DEBUG** (`-v`): Full API request/response details, matching decisions, timing

Logs should be written to both stderr (for console visibility) and an optional log file.

### Acceptance Criteria

- [ ] Default INFO-level logging to stderr
- [ ] `-v` flag enables DEBUG-level logging
- [ ] `--log-file` writes logs to file (in addition to stderr)
- [ ] Structured format: `YYYY-MM-DD HH:MM:SS LEVEL message`
- [ ] Sensitive data (tokens, passwords) never logged
- [ ] Per-group progress logging during parallel execution

---

## Story: CRIBL-110 — Documentation & Operational Runbooks

**Type:** Story
**Priority:** Medium
**Epic:** CRIBL-100
**Story Points:** 3
**Labels:** `docs`, `runbook`

### Summary

Create comprehensive documentation including README, technical docs, flowcharts, and troubleshooting guide.

### Description

Documentation deliverables:
1. **README.md** — Quick start, configuration reference, CLI reference, troubleshooting
2. **DOCUMENTATION.md** — Technical deep-dive: architecture, components, data flow, security
3. **FLOWCHART.md** — Mermaid diagrams: main flow, capture flow, auth flow, matching flow
4. **config.example.json** — Annotated configuration template
5. **.env.example** — Environment variable template with security notes

### Acceptance Criteria

- [ ] README with flow diagram, quick start, config reference, CLI reference
- [ ] Technical documentation covering all components and design decisions
- [ ] Mermaid flowcharts for main flow, capture, auth, and matching logic
- [ ] Troubleshooting section with common error messages and solutions
- [ ] `config.example.json` with all available options
- [ ] `.env.example` with all supported environment variables
- [ ] `.gitignore` protecting credentials and output files

---

## Bug/Task Templates

### BUG: CRIBL-1XX — [Template]

```
Type: Bug
Priority: [Critical/High/Medium/Low]
Epic: CRIBL-100
Labels: bug

Summary: [One-line description]

Steps to Reproduce:
1. ...
2. ...
3. ...

Expected Behavior:
...

Actual Behavior:
...

Environment:
- Python version:
- Cribl Stream version:
- OS:
- Auth method:

Logs:
[Attach relevant log output]
```

### TASK: CRIBL-1XX — [Template]

```
Type: Task
Priority: [High/Medium/Low]
Epic: CRIBL-100
Labels: task

Summary: [One-line description]

Description:
...

Acceptance Criteria:
- [ ] ...
- [ ] ...

Technical Notes:
...
```

---

## Dependency Graph

```
CRIBL-102 (Config) ─────┐
                         ├──> CRIBL-103 (API Client) ──> CRIBL-104 (Matching) ──> CRIBL-105 (Parallel)
CRIBL-101 (Auth) ────────┘                                                              │
                                                                                        v
                                                            CRIBL-106 (Dedup) ──> CRIBL-107 (Output)
                                                                                        │
                                                                                        v
                                                            CRIBL-108 (Inspect/Dry-run)
                                                            CRIBL-109 (Logging)
                                                            CRIBL-110 (Documentation)
```

### Sprint Recommendation

| Sprint | Stories | Focus |
|--------|---------|-------|
| Sprint 1 | CRIBL-101, CRIBL-102, CRIBL-103 | Foundation: auth, config, API client |
| Sprint 2 | CRIBL-104, CRIBL-105, CRIBL-106 | Core logic: matching, parallelism, dedup |
| Sprint 3 | CRIBL-107, CRIBL-108, CRIBL-109, CRIBL-110 | Output, UX modes, logging, docs |
