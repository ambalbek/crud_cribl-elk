# get_apmids_from_blob.py — Documentation

## Overview

`get_apmids_from_blob.py` is a **read-only audit tool** that detects application IDs (apmIds) falling through to the default Azure Blob Storage destination in Cribl Stream. It replaces the deprecated `get_apmids_from_elk.py` by reading directly from Azure Blob Storage instead of Elasticsearch (ELK), which is being decommissioned.

When an application lacks a dedicated Cribl route and destination, its data lands in a catch-all "default" blob container. This script finds those applications so teams can configure proper routing.

---

## How It Works

```
Azure Blob (default container)          Cribl Stream API
         |                                      |
   List blobs by date                   GET /outputs (azure_blob)
   Extract apmId from JSON              GET /routes
         |                                      |
         +-------------- MATCH ----------------+
                          |
              Status per apmId:
         CONFIGURED / MISSING_ROUTE /
       MISSING_DESTINATION / MISSING_BOTH
                          |
                 CSV + Console Output
```

### Three-Step Flow

| Step | What Happens |
|------|-------------|
| **1. Scan Blob Storage** | Lists all `CriblOut-*.json.gz` files in the default container for the last N days. Groups files by parent folder, picks the largest file per folder, downloads it in parallel, and extracts `apmId` from the gzipped JSON content. |
| **2. Fetch Cribl Config** | Authenticates to Cribl Stream API and retrieves all `azure_blob` type destinations and all routes for the specified worker group. |
| **3. Match & Report** | For each discovered apmId, checks if a matching destination (apmId in `containerName`) and route (apmId in route `name`) exist. Outputs a status table and optional CSV/JSON files. |

### Blob Path Pattern

```
{container}/{YYYY}/{MM}/{DD}/{appName}/{region}/{env}/CriblOut-*.json.gz
```

Example:
```
default/2026/06/28/MyApp/eastus/prod/CriblOut-0001.json.gz
```

---

## Usage

### Basic (today only)
```bash
python get_apmids_from_blob.py --config config.json
```

### Last 7 days, save CSV
```bash
python get_apmids_from_blob.py --config config.json --days 7 -o results.csv
```

### Filter by region and environment
```bash
python get_apmids_from_blob.py --config config.json --region eastus --env prod
```

### Full options
```bash
python get_apmids_from_blob.py --config config.json \
  --days 7 \
  --region eastus \
  --env prod \
  --max-blobs 100 \
  --workers 20 \
  --output results.csv \
  --json-output results.json \
  --debug
```

### CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--config` | *(required)* | Path to `config.json` |
| `--days` | `1` | Look back N days (1 = today only) |
| `--region` | *(none)* | Filter blobs by region (e.g., `eastus`) |
| `--env` | *(none)* | Filter blobs by environment (e.g., `prod`, `dev`) |
| `--max-blobs` | `0` (unlimited) | Cap on blobs to process |
| `--workers` | `10` | Parallel download threads |
| `--output`, `-o` | *(none)* | Save full results to CSV |
| `--json-output` | *(none)* | Save full results to JSON |
| `--debug` | `false` | Print verbose debug info |

---

## Configuration (`config.json`)

The script uses three sections from `config.json`:

### `blob_storage` (required)

Connect to Azure Blob Storage. Auth priority:

1. `connection_string` — full connection string
2. Service principal — `tenant_id` + `client_id` + `client_secret` (requires `azure-identity`)
3. `account_url` or `account_name` + `sas_token` or `account_key`
4. `DefaultAzureCredential` — managed identity / `az login` fallback (requires `azure-identity`)

```json
"blob_storage": {
  "connection_string": "",
  "account_url": "https://ACCOUNT.blob.core.windows.net",
  "account_name": "",
  "account_key": "",
  "sas_token": "",
  "tenant_id": "",
  "client_id": "",
  "client_secret": "",
  "container": "default"
}
```

### `auth` (required)

Cribl Stream API credentials. Auth priority:

1. `client_id` + `client_secret` — OAuth2 (Cribl Cloud)
2. `username` + `password` — leader login (self-managed)
3. `token` — static bearer token

```json
"auth": {
  "cribl_url": "https://main-myorg.cribl.cloud",
  "client_id": "",
  "client_secret": ""
}
```

### `capture.groups` (optional)

Specifies which worker group to query for destinations/routes. Defaults to `["default"]`; the script uses the first group in the list.

### `connection` (optional)

```json
"connection": {
  "verify_ssl": true
}
```

---

## Output

### Console Table

```
apmId     appName     Events   Has Dest   Destination      Has Route   Route     Status
-----------------------------------------------------------------------------------------------
APP001    MyApp            5        YES   my-dest-app001         YES   rt-app001   CONFIGURED
APP002    OtherApp         3         NO   NONE                    NO   NONE        MISSING_BOTH <<<
```

### CSV Files

When `--output results.csv` is specified, two files are created:

| File | Contents |
|------|----------|
| `results.csv` | All apmIds with full status |
| `results_missing_only.csv` | Only apmIds that are NOT fully configured (status != CONFIGURED) |

### Status Values

| Status | Meaning |
|--------|---------|
| `CONFIGURED` | Has both a dedicated destination and route |
| `MISSING_ROUTE` | Destination exists but no matching route |
| `MISSING_DESTINATION` | Route exists but no matching destination |
| `MISSING_BOTH` | No destination and no route — completely untracked |

---

## Scheduled Daily Job

### Why Schedule It?

New applications onboard continuously. Without daily checks, untracked apmIds accumulate silently in the default container, leading to data governance gaps, compliance risk, and storage cost bloat. A daily scheduled run catches these within 24 hours.

### Cron Job (Linux / WSL)

Add to crontab (`crontab -e`):

```cron
# Run daily at 6:00 AM UTC — scan previous day's blobs
0 6 * * * cd /path/to/untracked_apmids && python get_apmids_from_blob.py --config config.json --days 1 -o /path/to/reports/blob_audit_$(date +\%Y\%m\%d).csv >> /path/to/logs/blob_audit.log 2>&1
```

### Windows Task Scheduler

1. Open **Task Scheduler** > **Create Task**
2. **Trigger**: Daily at 6:00 AM
3. **Action**: Start a program
   - Program: `python`
   - Arguments: `get_apmids_from_blob.py --config config.json --days 1 -o C:\reports\blob_audit.csv`
   - Start in: `C:\path\to\untracked_apmids`

### PowerShell Scheduled Task

```powershell
$action = New-ScheduledTaskAction `
  -Execute "python" `
  -Argument "get_apmids_from_blob.py --config config.json --days 1 -o C:\reports\blob_audit_$(Get-Date -Format yyyyMMdd).csv" `
  -WorkingDirectory "C:\path\to\untracked_apmids"

$trigger = New-ScheduledTaskTrigger -Daily -At 6:00AM

Register-ScheduledTask `
  -TaskName "CriblBlobAudit" `
  -Action $action `
  -Trigger $trigger `
  -Description "Daily audit of untracked apmIds in Cribl default blob container"
```

### CI/CD Pipeline (GitHub Actions Example)

```yaml
name: Daily Blob Audit
on:
  schedule:
    - cron: '0 6 * * *'  # 6:00 AM UTC daily
  workflow_dispatch:       # manual trigger

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - run: pip install requests azure-storage-blob azure-identity

      - name: Run blob audit
        env:
          AZURE_STORAGE_CONNECTION_STRING: ${{ secrets.AZURE_BLOB_CONN_STRING }}
        run: |
          python get_apmids_from_blob.py \
            --config config.json \
            --days 1 \
            -o blob_audit_$(date +%Y%m%d).csv

      - name: Upload report
        uses: actions/upload-artifact@v4
        with:
          name: blob-audit-report
          path: blob_audit_*.csv
          retention-days: 30
```

---

## Gains and Benefits

### 1. Data Governance & Compliance

| Benefit | Detail |
|---------|--------|
| **Catch untracked applications** | Identifies every apmId landing in the default catch-all container — data that has no dedicated routing. |
| **Close the governance gap** | Ensures all application data flows through properly configured destinations with correct retention, access, and encryption policies. |
| **Audit trail** | CSV/JSON output provides a timestamped record of what was found, supporting compliance audits (SOC 2, ISO 27001, GDPR data mapping). |

### 2. Cost Optimization

| Benefit | Detail |
|---------|--------|
| **Reduce default container bloat** | Untracked data accumulates in the default container indefinitely. Identifying it allows teams to route it properly or stop ingesting it. |
| **Right-size storage** | Data routed to dedicated containers can have appropriate retention policies (hot/cool/archive tiers), reducing Azure storage costs. |
| **Avoid duplicate storage** | Some untracked apmIds may already have proper destinations — the `MISSING_ROUTE` status reveals the gap without re-creating the destination. |

### 3. Operational Visibility

| Benefit | Detail |
|---------|--------|
| **Daily awareness** | Scheduled runs surface new untracked applications within 24 hours of first data arrival. |
| **Actionable status categories** | Four clear statuses (`CONFIGURED`, `MISSING_ROUTE`, `MISSING_DESTINATION`, `MISSING_BOTH`) tell teams exactly what to fix. |
| **Trend tracking** | Comparing daily reports over time shows onboarding velocity and governance posture improvement. |

### 4. ELK Independence

| Benefit | Detail |
|---------|--------|
| **No ELK dependency** | Reads directly from Azure Blob Storage — no reliance on the deprecated ELK cluster. |
| **Simpler infrastructure** | One fewer service to maintain; the data source (blob) is already the production artifact. |
| **Faster and cheaper** | Blob listing + targeted downloads is more efficient than querying an Elasticsearch index. |

### 5. Safety

| Benefit | Detail |
|---------|--------|
| **Completely read-only** | Uses only `GET` API calls to Cribl and read-only blob operations. Zero risk of modifying Cribl config or data. |
| **Parallel but safe** | Thread-safe auth with locking; HTTP retries with backoff for transient failures. |
| **Graceful filtering** | Region/env filters and `--max-blobs` cap prevent accidental overload on large containers. |

---

## Dependencies

```
pip install requests azure-storage-blob
```

Optional (for service principal or managed identity auth):
```
pip install azure-identity
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success — all apmIds processed |
| `1` | Fatal error (bad config, auth failure, missing dependencies) |
