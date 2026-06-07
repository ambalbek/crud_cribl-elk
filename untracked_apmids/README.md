# find_default_appids — Untracked AppId Detector for Cribl Stream

Discovers appIds actively falling to the default Azure Blob destination in Cribl Stream — i.e., appIds with no dedicated route/destination.

**Read-only** — only uses `GET /system/outputs` and transient `POST /system/capture`. No config is created, modified, or persisted.

---

## Flow

```
                          +-------------------+
                          |   Start           |
                          +--------+----------+
                                   |
                          +--------v----------+
                          | Load config.json  |
                          | Load .env file    |
                          +--------+----------+
                                   |
                          +--------v----------+
                          | Authenticate      |
                          | (OAuth2 / login / |
                          |  static token)    |
                          +--------+----------+
                                   |
                    +--------------+--------------+
                    |              |              |
                    v              v              v
              +----------+  +----------+  +----------+
              | Group 1  |  | Group 2  |  | Group N  |  (parallel)
              +----+-----+  +----+-----+  +----+-----+
                   |              |              |
            +------v------+      |              |
            | Round 1/N   |      |              |
            | POST        |      |              |
            | /capture    |      |              |
            | Extract     |      |              |
            | apmIds      |      |              |
            +------+------+      |              |
                   |              |              |
            +------v------+      |              |
            | Round 2/N   |      .              .
            | ...         |      .              .
            +------+------+
                   |
            +------v------+
            | GET outputs |
            | Match appId |
            | to dest     |
            +------+------+
                   |
                   +------------>+-----------+
                                 | Merge all |
                                 | groups    |
                                 +-----+-----+
                                       |
                              +--------v----------+
                              | Load lookup       |
                              | (APP_foo.json)    |
                              | Exclude appIds    |
                              | in azure_storage_ |
                              | account_containers|
                              +--------+----------+
                                       |
                              +--------v----------+
                              | Load previous CSV |
                              | Exclude known     |
                              | appIds            |
                              +--------+----------+
                                       |
                              +--------v----------+
                              | Show only NEW     |
                              | untracked appIds  |
                              +--------+----------+
                                       |
                         +-------------+-------------+
                         |             |             |
                    +----v----+  +----v----+  +-----v------+
                    |  CSV    |  |  JSON   |  | Elastic-   |
                    |  output |  |  output |  | search     |
                    +---------+  +---------+  | bulk index |
                                              +------------+
```

---

## Requirements

- Python 3.9+
- `pip install requests`

---

## Quick Start

```bash
# 1. Create config
cp config.example.json config.json
# Edit config.json — fill in auth, groups, ES details

# 2. Validate
python find_default_appids.py --config config.json --dry-run

# 3. Inspect (discover field names, verify events flow)
python find_default_appids.py --config config.json --inspect

# 4. Run
python find_default_appids.py --config config.json
```

---

## Configuration

### config.json

```json
{
  "auth": {
    "cribl_url": "https://leader:9000",
    "username": "admin",
    "password": "secret"
  },
  "capture": {
    "groups": ["prod-worker-group"],
    "seconds": 30,
    "max_events": 5000,
    "level": 3,
    "rounds": 10,
    "interval": 120,
    "appid_field": "apmId"
  },
  "matching": {
    "mode": "exact",
    "default_output": "azure_blob:foo-company-default"
  },
  "output": {
    "format": "csv",
    "append": false,
    "lookup": "APP_foo.json"
  },
  "elasticsearch": {
    "url": "https://elk.internal:9200",
    "index": "cribl-untracked-appids",
    "api_key": ""
  },
  "logging": {
    "log_file": "find_default_appids.log",
    "verbose": false
  },
  "connection": {
    "verify_ssl": true
  }
}
```

### Priority Order

Values are resolved in this order (first wins):

```
CLI args  >  config.json  >  environment variables  >  hardcoded defaults
```

### Auth Methods (checked in order)

| Method | Config keys | Env vars |
|--------|------------|----------|
| Cribl Cloud OAuth2 | `auth.client_id` + `auth.client_secret` | `CRIBL_CLIENT_ID` + `CRIBL_CLIENT_SECRET` |
| Leader login | `auth.username` + `auth.password` | `CRIBL_USERNAME` + `CRIBL_PASSWORD` |
| Static token | `auth.token` | `CRIBL_TOKEN` |

`auth.cribl_url` / `CRIBL_URL` is always required.

---

## Lookup Table

The `--lookup` option (or `output.lookup` in config) points to a JSON file that lists appIds which already have Azure containers. These are excluded from results.

**Example `APP_foo.json`:**
```json
{
  "azure_storage_account_containers": [
    "app-one",
    "app-two",
    "app-three"
  ],
  "other_key": "ignored"
}
```

Only `azure_storage_account_containers` is read. Matching is case-insensitive.

---

## Output

### Console Table

```
apmId          appName     outputId                            Events   Matched Destination
------------------------------------------------------------------------------------------
my-new-app     MyApp       azure_blob:foo-company-default          42   DEFAULT <<<
another-app    Other       azure_blob:foo-company-default          15   DEFAULT <<<
```

`<<<` marks unmatched (DEFAULT) appIds.

### CSV

Timestamped: `appids_without_destination_20260607_143022.csv`

| Column | Description |
|--------|-------------|
| apmId | The appId value |
| appName | Application name (if available) |
| outputId | Cribl output the event was heading to |
| matched_destination | Destination ID or `DEFAULT` |
| event_count | Number of events seen for this combo |

### JSON

Same data as CSV, plus metadata (timestamp, group, totals).

### Elasticsearch

Each appId is indexed as a separate document:

```json
{
  "@timestamp": "2026-06-07T14:30:00+00:00",
  "group": "prod-worker-group",
  "apmId": "my-new-app",
  "appName": "MyApp",
  "outputId": "azure_blob:foo-company-default",
  "matched_destination": "DEFAULT",
  "is_unmatched": true,
  "is_new": true,
  "event_count": 42,
  "total_events_captured": 5000
}
```

ES auth options: `ES_API_KEY` (recommended), or `ES_USERNAME` + `ES_PASSWORD`.

---

## CLI Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--config PATH` | | JSON config file |
| `--group G [G...]` | | Worker group(s) — multiple run in parallel |
| `--filter EXPR` | auto-detect | JavaScript capture filter |
| `--default-output ID` | auto-detect | Default output ID |
| `--seconds N` | 30 | Capture duration per round |
| `--max-events N` | 5000 | Max events per round |
| `--level {0,1,2,3}` | 3 | Capture stage |
| `--appid-field FIELD` | apmId | Field path for appId |
| `--match-mode MODE` | exact | `exact`, `contains`, or `partition` |
| `--output PATH` | timestamped .csv | Output file path |
| `--append` | false | Append to CSV (deduplicates) |
| `--rounds N` | 1 | Number of capture rounds |
| `--interval N` | 60 | Seconds between rounds |
| `--format FMT` | csv | `csv`, `json`, or `both` |
| `--lookup PATH` | | Lookup JSON to exclude known containers |
| `--diff-csv PATH` | auto-detect | Previous CSV to diff against |
| `--es-url URL` | | Elasticsearch URL |
| `--es-index NAME` | | Elasticsearch index |
| `--inspect` | | Discovery mode — sample events, show fields |
| `--dry-run` | | Validate config without capturing |
| `--env-file PATH` | | Load .env file for credentials |
| `--log-file PATH` | | Write logs to file |
| `--no-verify-ssl` | false | Disable SSL verification |
| `-v` | false | Debug logging |

---

## Capture Levels

| Level | Stage |
|-------|-------|
| 0 | Before pre-processing pipeline |
| 1 | Before routes |
| 2 | Before post-processing pipeline |
| 3 | Before destination (default) |

---

## Match Modes

| Mode | Behavior |
|------|----------|
| `exact` | `containerName == appId` (case-insensitive) |
| `contains` | `appId` appears in `containerName` |
| `partition` | `containerName` exact match OR `appId` in `partitionExpr` |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Fatal error (auth, connectivity, no events) |
| 2 | Partial failure (some groups failed, others succeeded) |
| 130 | Interrupted (Ctrl+C) — partial results saved |

---

## Filtering Logic

An appId is shown in output only if **all** of these are true:

1. It was captured in live events heading to the default output
2. It does NOT match any existing `azure_blob` destination (via match mode)
3. It does NOT exist in the lookup file (`azure_storage_account_containers`)
4. It does NOT exist in a previous CSV run

---

## Troubleshooting

### "Group 'X': expected JSON but got..."
The group name doesn't exist in Cribl, or `CRIBL_URL` is wrong.

### "Login endpoint returned non-JSON response"
`CRIBL_URL` is wrong — should be the base URL (e.g. `https://leader:9000`), not include `/api/v1`.

### ES: "Authentication failed (HTTP 401)"
Set `ES_API_KEY` or `ES_USERNAME` + `ES_PASSWORD` in config or env.

### ES: "Authorization denied (HTTP 403)"
The ES user/key doesn't have write access to the index. Grant `index` privilege.

### ES: "returned non-JSON"
`ES_URL` points to Kibana, a proxy, or a non-ES service. Use the ES API URL (usually port 9200).

### No events captured
- No traffic hitting the default output right now
- Wrong filter expression — run `--inspect` to discover the right one
- Duration too short — try `--seconds 60`

### Use `--dry-run` to validate everything before a real run.
