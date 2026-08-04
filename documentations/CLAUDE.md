# Untracked AppId Detector for Cribl Stream

## What This Project Does
Read-only audit tool that finds application IDs (appIds) falling through to the default Azure Blob destination in Cribl Stream — meaning they have no dedicated route/destination configured. This is a data governance gap detector.

## Key Constraint
**Completely read-only** — only uses `GET` endpoints and transient `POST /system/capture`. No Cribl configuration is created, modified, or persisted.

## Architecture

### Entry Points
- `find_default_appids.py` — 2026-line monolithic standalone script (main entry point)
- `get_apmids_from_elk.py` — 587-line alternate script that sources data from ELK instead of Cribl capture **(deprecated — ELK decommissioned)**
- `get_apmids_from_blob.py` — ELK replacement, reads directly from Azure Blob default container (`{YYYY}/{MM}/{DD}/{appName}/{region}/{env}/CriblOut-*.json.gz`)
- `cribl_audit/` — Modularized package version (same logic, split into 14 files). **Ignored by Claude** — all work should happen in the top-level scripts.

### Core Flow
```
Config/Auth → Capture events from Cribl worker groups (parallel) → Extract apmIds → Match against destinations → Deduplicate via lookup table + prior CSV → Output new unmatched appIds
```

### Operational Modes
| Mode | Flag | Purpose |
|------|------|---------|
| Full Analysis | (default) | Complete audit with output |
| Dry Run | `--dry-run` | Validate auth/connectivity only |
| Inspect | `--inspect` | Discover fields, suggest filters |

## Tech Stack
- **Python 3.9+**, dependencies: `requests`, `azure-storage-blob` (for blob script)
- **Cribl Stream REST API v1** — GET /outputs, GET /routes, POST /capture
- **Auth**: OAuth2 (Cribl Cloud), leader login (self-managed), or static bearer token
- **Output**: CSV, JSON, console table, Elasticsearch bulk API
- **Parallelism**: `ThreadPoolExecutor` for multi-group processing
- **Config resolution**: CLI args > config.json > environment variables > defaults

## Important Context
- **ELK is being decommissioned.** The `elasticsearch.py` module and `get_apmids_from_elk.py` will need replacement backends. Do not build new features on ELK.
- Replacement candidates: Cribl Search/Lake, SQLite/DuckDB for local persistence, webhook/alerting integrations.
- Config lives in `config.json` (gitignored) and `.env` (gitignored). Templates at `config.example.json` and `.env.example`.

## File Reference
| File | Purpose |
|------|---------|
| `find_default_appids.py` | Main standalone script |
| `get_apmids_from_elk.py` | ELK-source alternate (deprecated) |
| `get_apmids_from_blob.py` | Blob-source alternate (ELK replacement) |
| `config.example.json` | Config template with all sections |
| `.env.example` | Environment variable template |
| `test.py`, `test_modules.py`, `test_validate.py` | Tests |
| `DOCUMENTATION.md` | 682-line technical deep-dive |
| `FLOWCHART.md` | Mermaid flow diagrams |

## When Modifying Code
- Keep the tool **read-only** — never add write/mutate calls to Cribl APIs
- Thread safety matters — auth tokens and output buffers use locks
- The monolithic `find_default_appids.py` is the source of truth; `cribl_audit/` is a modular mirror
- Match modes (exact/contains/partition) are case-insensitive
- Exit codes: 0 = success, 1 = fatal, 2 = partial success, 130 = Ctrl+C interrupted
