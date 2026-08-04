# Phase 0 — Audit

> Generated 2026-07-27. Verified against commit `a95676d` (HEAD of local `main`).

## 0. Corrections to the upgrade-prompt §0 summary

| Claim in prompt | Actual |
|---|---|
| `cribl_service` on port 8001 | Docstring says `--port 8000`; docker-compose.yml maps host 8001 → container 8000. Both are correct depending on context. |
| `ece_service` on port 8002 | Docstring says `--port 8002`; docker-compose maps host 8002 → container 8002. Consistent. |
| State lives in ES index `cribl-onboarding-requests` | Config default is `cribl-onboarding-requests`; app.py reads from `config.datastream.index`. Correct. |
| Request IDs `REQ-YYYYMMDD-XXXXXXXX` | Confirmed — generated in `app.py` portal_submit via `uuid.uuid4().hex[:8].upper()`. |

No material inaccuracies found. The table is accurate.

---

## 1. File/Module inventory vs. README claims

The tracked `README.md` has been **deleted from the working tree** (unstaged `git rm`). A replacement lives in the untracked `documentations/README.md`. Below is the actual file inventory:

### Root-level Python modules

| File | Purpose | Mentioned in docs? |
|---|---|---|
| `app.py` (84 KB, ~2100 lines) | Flask portal: auth, onboarding portal, pusher UI, entitlement lookup, service catalog, health checks | Yes |
| `cribl-pusher.py` (~21 KB) | CLI — route + destination upsert to Cribl workspaces | Yes |
| `role_rm.py` (~25 KB) | CLI — ELK role/role-mapping template generation + push; also Cribl route push | Yes |
| `cribl_api.py` | Cribl API helpers (login, normalize_route, find_default_route, group handling, count_all_routes) | Yes |
| `cribl_config.py` | Config loader + workspace/template/credential resolution | Yes |
| `cribl_utils.py` | Shared utils (die, short_id, diff, read_json, prompts, make_session) | Yes |
| `cribl_logger.py` | Named-logger factory (`setup_logging`, `get_logger`) | Yes |
| `otel_setup.py` | OTel TracerProvider + JSON formatter + auto-instrumentation | Partially |
| `_validate.py` | Offline test suite (18 sections, PASS/FAIL). Not a pytest suite. | No |
| `generate_pptx.py` | PowerPoint generation (gitignored but present on disk) | No |

### Service directories

| Directory | Key files | Notes |
|---|---|---|
| `cribl_service/` | `main.py`, `deps.py` (sync CriblClient, 432 lines), `cribl_client.py` (async, 471 lines), `models.py` (247 lines), `config.py`, `settings.py`, 10 routers | Fully functional |
| `ece_service/` | `main.py`, `deps.py` (sync ECEClient, 341 lines), `ece_client.py` (async, 172 lines), `models.py` (217 lines), `config.py`, `settings.py`, 6 routers | Fully functional |
| `entitlement/` | Separate mini Flask app with its own `requirements.txt` (flask 3.0.0, requests 2.31.0) | Older pinned versions; unclear if still used vs. the entitlement page in app.py |
| `etn_onboarding/` | **Untracked.** Full Flask app scaffold with SQLAlchemy, Alembic, Helm chart, Dockerfile. Has `flask-sqlalchemy`, `flask-migrate`, `psycopg2-binary` in requirements.txt. | Appears to be the start of the Postgres-backed redesign. Not integrated with the main services yet. |

### Configuration / Templates

| File | Tracked? | Notes |
|---|---|---|
| `config.json` | **YES — tracked in git** | .gitignore lists it, but it was committed before the rule was added. See §2. |
| `config.example.json` | Yes | Template with placeholder values |
| `.env` | No (correctly gitignored) | Present on disk with example-like values |
| `.env.example` | Yes | Template |
| `route_template_az{n,s}.json` | Yes | Route templates for north/south regions |
| `blob_dest_template_az{n,s}_{dev,test,prod}.json` | Yes (6 files) | Destination templates per region × environment |
| `elk-index-template.json` | Yes | ES index template |
| `elk-role.json` | Yes | ES role template |

### Binary blobs

| File | Size | Tracked? |
|---|---|---|
| `app-images.tar.gz.part-aa` | 50 MB | Yes |
| `app-images.tar.gz.part-ab` | 50 MB | Yes |
| `app-images.tar.gz.part-ac` | 46 MB | Yes |
| `Cribl_GitOps_Justification.pptx` | 936 KB | Yes (in git history via `etn/`) |
| `Cribl_GitOps_ARO_Demo.pptx` | 61 KB | In untracked `documentations/` |

### Documentation drift

- The tracked `README.md` was deleted from the working tree, replaced by `documentations/README.md` (untracked).
- `c4-diagrams.md`, `flowchart.md`, `flowchart-gitops-aro.md`, `flowchart-python.md`, `flowchart-visio.md` were all deleted from tracked paths and moved to `documentations/`.
- `docs/` directory exists but is empty — the azure-container-gitops plan is in `documentations/`.
- `untracked_apmids/` contains test scripts and docs that were tracked but are now deleted from working tree.

---

## 2. Repo hygiene problems

### 2a. `config.json` committed with credentials in git history

**Confirmed.** `config.json` appears in 3 commits:

```
afae252  updated flow
cf01720  first commit
4efa718  first commit
```

The `.gitignore` lists `config.json`, but the file was committed *before* the gitignore rule was added. Git continues to track it.

**What's in the committed version:**
- `"password": "user123"` for the test user account
- `"username": "elastic"`, `"password": "changeme"` in the entitlement cluster config
- `"secret_key": "CHANGE_ME_GENERATE_A_RANDOM_64_CHAR_STRING"` (placeholder)
- `"admin_secret": "CHANGE_ME_TO_A_STRONG_RANDOM_STRING"` (placeholder)

The committed version uses placeholder/example values, not obviously real production credentials. The *working copy* `config.json` is nearly identical (credentials fields are empty or use the same placeholders).

**Remediation (do not run these — for your review):**

```bash
# 1. Stop tracking config.json without deleting it from disk
git rm --cached config.json
git commit -m "chore: stop tracking config.json (already in .gitignore)"

# 2. If real credentials were EVER committed (check all commits):
#    Rewrite history to purge config.json from all commits:
pip install git-filter-repo
git filter-repo --invert-paths --path config.json

# 3. After history rewrite, force-push and have all collaborators re-clone.
# 4. Rotate any credentials that were ever in the file:
#    - Elasticsearch elastic/changeme password
#    - Any Cribl tokens/passwords
#    - Flask secret_key
#    - admin_secret
```

### 2b. Binary blobs in the repo

**Confirmed.** Three split tar.gz parts total ~146 MB of container images in git history:

- `app-images.tar.gz.part-aa` (50 MB)
- `app-images.tar.gz.part-ab` (50 MB)
- `app-images.tar.gz.part-ac` (46 MB)

`flask-cribl.tar` also appears in git history (committed at `8a96080`, `4efa718`).

**Recommendation:**
1. Remove from git history via `git filter-repo --invert-paths --path 'app-images.tar.gz.part-*' --path flask-cribl.tar`
2. Push container images to **Nexus Container Registry** (or Azure Container Registry) instead.
3. If tar archives are still needed for air-gapped transfers, use Git LFS or a shared network drive.

### 2c. Additional hygiene issues

- **`.gitignore` contradictions:** `config.json` is listed but tracked. `generate_pptx.py` is gitignored but present and was committed. `elasticsearch.yml` is gitignored but present.
- **Duplicate client implementations:** `cribl_service/deps.py` (sync) and `cribl_service/cribl_client.py` (async) implement the same logic. Same for `ece_service/deps.py` vs `ece_service/ece_client.py`. The async versions are used by newer routers (stream, edge, workgroups, ilm); the sync versions by the original routers.
- **`entitlement/` has stale pins:** `flask==3.0.0`, `requests==2.31.0` — behind the root `requirements.txt`.
- **No lockfile** (`requirements.lock`, `pip-compile` output, or `poetry.lock`) for reproducible builds.
- **Commit messages are all generic:** "added", "update", "first commit" — no conventional commits, no context.

---

## 3. Credential inventory

Every place a credential is read and where it comes from:

| Credential | Read by | Source | Notes |
|---|---|---|---|
| Flask `secret_key` | `app.py:189` | `config.json → secret_key` | Hardcoded fallback: `"CHANGE-ME-insecure-default"` |
| Local admin user/pass | `app.py:199-211` | `config.json → auth.local_admins[]` | Plaintext comparison |
| Local user/pass | `app.py:199-211` | `config.json → auth.local_users[]` | Plaintext comparison |
| `admin_secret` | `app.py:669` | `config.json → admin_secret` | Used for API auth on portal status updates |
| Cribl token/user/pass | `app.py`, `cribl-pusher.py`, `cribl_config.py` | `config.json → credentials.*`, CLI flags, env vars | Priority: CLI > env > config |
| Cribl token/user/pass | `cribl_service/settings.py` | `CRIBL_TOKEN`, `CRIBL_USERNAME`, `CRIBL_PASSWORD` env vars | No config.json fallback |
| ES datastream token/user/pass | `app.py:274-303` | `config.json → datastream.*` | Used for onboarding index CRUD |
| ES nonprod token/user/pass | `ece_service/settings.py` | `ECE_ES_TOKEN`, `ECE_ES_USERNAME`, `ECE_ES_PASSWORD` env vars | |
| ES prod token/user/pass | `ece_service/settings.py` | `ECE_ES_*_PROD` env vars | Falls back to nonprod if unset |
| Kibana token/user/pass | `ece_service/settings.py` | `ECE_KIBANA_TOKEN`, `ECE_KIBANA_USERNAME`, `ECE_KIBANA_PASSWORD` env vars | |
| Entitlement cluster creds | `app.py:946-948` | `config.json → entitlement.clusters[].username/password/token` | Per-cluster, used for role-mapping lookups |
| IIQ URL | `app.py` | `config.json → iiq_url` | Not a secret per se, but an internal URL |

**Patterns:**
- `app.py` reads credentials from `config.json` (a committed file).
- `cribl_service` and `ece_service` read credentials exclusively from environment variables (correct pattern).
- `cribl-pusher.py` and `role_rm.py` accept credentials via CLI flags, env vars, or `config.json` (in that priority order).
- No credential is encrypted at rest anywhere. No Key Vault integration exists yet.

---

## 4. Coupling map

### Direct imports in `app.py`

```
app.py
├── cribl_config  (get_dest_prefix, get_dest_template_path, get_route_template_path,
│                   get_workspace, build_workspace_urls)
├── cribl_utils   (read_json, read_apps_from_file)
└── otel_setup    (configure_otel, make_json_formatter, use_json_logging)
```

### HTTP calls from `app.py`

```
app.py
├──→ CRIBL_SERVICE_URL/api/v1/m/{wg}/provision    (when env var set)
├──→ ECE_SERVICE_URL/api/v1/roles/provision        (when env var set)
├──→ Elasticsearch (direct)
│    ├── POST /{index}/_doc                         (submit request)
│    ├── POST /{index}/_update_by_query             (update status)
│    ├── POST /{index}/_search                      (catalog fetch)
│    ├── GET /_cluster/health                       (health check)
│    ├── GET /_cat/indices/otel-*                    (OTel traces)
│    ├── GET /_security/role_mapping                 (entitlement lookup)
│    ├── DELETE /_security/role_mapping/{name}        (offboard)
│    ├── DELETE /_security/role/{name}                (offboard)
│    └── GET /{pattern}/_ilm/explain                 (ILM tier lookup)
├──→ Cribl (direct)
│    ├── POST /api/v1/auth/login                     (auth)
│    ├── GET /api/v1/m/{wg}/routes/{table}           (catalog + offboard)
│    └── PATCH /api/v1/m/{wg}/routes/{table}         (offboard)
├──→ Logstash GET /                                  (health)
└──→ Kibana GET /api/status                          (health)
```

### Subprocess calls from `app.py`

```
app.py
├── subprocess.run(["python", "cribl-pusher.py", ...])   (when CRIBL_SERVICE_URL not set)
└── subprocess.run(["python", "role_rm.py", ...])         (when ECE_SERVICE_URL not set)
```

**Key observation:** `app.py` has a dual-path architecture. When microservice URLs are configured (docker-compose), it calls them over HTTP. When running standalone, it falls back to subprocess invocation of the CLIs. This means `app.py` also makes **direct** Elasticsearch and Cribl calls for catalog, health, entitlements, and offboarding — bypassing the microservices even when they're available.

---

## 5. Dependency audit

### Root `requirements.txt` (Flask portal)

| Package | Pinned | Latest (as of 2026-07) | Issue |
|---|---|---|---|
| `flask` | 3.1.0 | 3.1.x | OK |
| `requests` | 2.32.3 | 2.32.x | OK |
| `urllib3` | 2.3.0 | 2.3.x | OK |
| `jinja2` | >=3.1.0 | 3.1.x | OK (floor pin) |
| OTel packages | unpinned | varies | Should pin to avoid breaking changes |
| `python-json-logger` | >=2.0 | 3.x exists | Floor pin, should verify compat |

### `cribl_service/requirements.txt`

| Package | Pinned | Issue |
|---|---|---|
| `fastapi` | 0.115.0 | FastAPI 0.115 is current. OK. |
| `uvicorn[standard]` | 0.30.6 | OK |
| `requests` | 2.32.3 | OK |
| `urllib3` | 2.3.0 | OK |
| `httpx` | 0.27.2 | OK |
| `pydantic-settings` | 2.3.4 | OK |
| OTel packages | unpinned | Same as above |

### `ece_service/requirements.txt`

Same as `cribl_service` plus `jinja2>=3.1.0`. Same unpinned OTel concern.

### `entitlement/requirements.txt`

| Package | Pinned | Issue |
|---|---|---|
| `flask` | 3.0.0 | **Behind root (3.1.0)** |
| `requests` | 2.31.0 | **Behind root (2.32.3)** |

### `etn_onboarding/requirements.txt` (untracked)

Uses floor pins (`>=`). Includes `flask-sqlalchemy>=3.1.0`, `flask-migrate>=4.0.0`, `sqlalchemy>=2.0`, `psycopg2-binary>=2.9`, `gunicorn>=22.0`. These are appropriate for a new project but should be pinned for reproducible builds.

### Missing dependencies (not declared anywhere)

- `azure-storage-blob` and `azure-identity` — mentioned in upgrade prompt but not present
- No test framework (`pytest`) in any requirements file

### Vulnerability notes

- No known CVEs in the pinned versions at time of writing.
- `urllib3` 2.3.0 and `requests` 2.32.3 are current.
- The bigger risk is the unpinned OTel stack — a bad release could break all three services simultaneously.

---

## 6. Test coverage

**Near zero. Confirmed.**

- No `pytest`, `unittest`, or any test framework in any `requirements.txt`.
- No `tests/` directory anywhere in the project.
- No `conftest.py`, no `pytest.ini`, no `tox.ini`, no `setup.cfg` with test config.
- `_validate.py` is a hand-rolled validation script (18 test sections) that tests shared module functions offline. It is **not** a pytest suite — it uses print statements, manual PASS/FAIL tracking, and `sys.exit()`. It does not test any HTTP endpoints, Flask routes, or FastAPI routes.
- `untracked_apmids/test*.py` files exist but are scratch/local scripts, not a test suite.
- No CI pipeline exists (no `.github/workflows/`, no `Jenkinsfile`, no `azure-pipelines.yml`).

**Bottom line:** There are zero automated tests for the Flask portal, zero for the FastAPI services, and zero integration tests. The only validation is `_validate.py` which covers shared pure-logic functions.
