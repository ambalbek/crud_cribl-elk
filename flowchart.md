# Cribl Framework — Flowcharts

## Service Architecture

> How the three containers relate at runtime.

```mermaid
flowchart LR
    subgraph HOST["Host (bastion)"]
        APACHE["Apache httpd\n(TLS termination\nport 443)"]
    end

    subgraph COMPOSE["Docker Compose network"]
        FLASK["cribl-framework\nFlask :5000\nPortal · Pusher UI · Catalog\nEntitlements · RBAC"]
        CS["cribl_service\nFastAPI :8001\nRoutes · Destinations · Pipelines\nWorker Groups · Leaders\nStream CRUD · Edge Fleets\nWorkgroups (async httpx)"]
        ES_SVC["ece_service\nFastAPI :8002\nES Roles · Role-Mappings\nIndexes · ILM Policies\nLogstash Pipelines\nKibana Dashboards\n(sync + async)"]
    end

    CRIBL[("Cribl Stream\n(external)")]
    ELK_D[("Elasticsearch\nDatastream\n(external)")]
    ELK_ENT[("Elasticsearch\nEntitlements\n(external)")]
    AZURE[("Azure Blob\nStorage\n(external)")]

    APACHE -->|"ProxyPass :5000"| FLASK
    FLASK -->|"REST HTTP :8001"| CS
    FLASK -->|"REST HTTP :8002"| ES_SVC
    CS -->|"Cribl REST API"| CRIBL
    FLASK -->|"ES REST API"| ELK_D
    FLASK -->|"ES REST API"| ELK_ENT
    CRIBL -->|"streams logs to"| AZURE
```

---

## End-to-End Onboarding Flow

```mermaid
flowchart TD
    CLIENT([Client logs in]) --> PORTAL["/cribl/portal — Onboarding Form\nUsername + Name auto-populated\nAPM ID, App Name, Region,\nLog Dest, Log Type, Groups"]
    PORTAL --> ES_INDEX["POST /cribl/portal/api/submit\n→ Index to Elasticsearch\n→ Returns REQ-YYYYMMDD-XXXXXXXX"]
    ES_INDEX --> PENDING["ES Document\nstatus = pending"]

    PLATFORM([Platform Team]) --> PUSHER["/cribl/app — Cribl Pusher\nPaste Request ID + App details"]
    PUSHER --> DRYRUN{Dry Run?}
    DRYRUN -- Yes --> PREVIEW["Preview diff\nNo writes"]
    DRYRUN -- No --> EXECUTE

    subgraph EXECUTE[Execute — via cribl_service]
        direction TB
        DEST["POST destinations\nto cribl_service"] --> ROUTES["PATCH routes\nvia cribl_service"]
        ROUTES --> STATUS["Auto-update ES\nstatus = done"]
    end

    EXECUTE --> DONE["ES Document\nstatus = done"]
    PREVIEW --> PUSHER
```

---

## Portal Submit Flow

```mermaid
flowchart TD
    START([Client opens /cribl/portal]) --> FORM["Username + Name from session\nFill: APM ID, App Name,\nRegion, Log Dest, Log Type, Groups"]
    FORM --> VALIDATE{Client-side\nvalidation}
    VALIDATE -- Errors --> SHOW_ERR[Show error list]
    SHOW_ERR --> FORM
    VALIDATE -- OK --> POST["POST /cribl/portal/api/submit\nJSON body"]

    POST --> SRV_VAL{Server-side\nvalidation}
    SRV_VAL -- Errors --> RET_400["400 + errors JSON"]
    SRV_VAL -- OK --> LOAD_CFG[Load config.json]
    LOAD_CFG --> BUILD_DOC["Build ES document:\n@timestamp, request_id,\nlan_id, requester_name,\napmid, appname, region,\nlog_destinations, log_types,\nentitlement_groups,\nstatus = pending"]
    BUILD_DOC --> ES_WRITE["POST to ES\ndatastream.elk_url / index"]
    ES_WRITE --> ES_OK{Success?}
    ES_OK -- No --> RET_500["500 + error"]
    ES_OK -- Yes --> RET_200["200 + request_id\nREQ-YYYYMMDD-XXXXXXXX"]
    RET_200 --> SUCCESS([Show success + Request ID])
```

---

## Cribl Pusher Flow (app.py /cribl/api/run-pusher)

```mermaid
flowchart TD
    START([POST /cribl/api/run-pusher]) --> PARSE["Parse form data:\nworkspace, worker_groups,\nregion, mode, request_id"]
    PARSE --> FVAL{Validation}
    FVAL -- Errors --> RET_400["400 + errors"]
    FVAL -- OK --> LOAD_CFG[Load config.json]
    LOAD_CFG --> SVC_CHECK{CRIBL_SERVICE_URL\nset?}

    SVC_CHECK -- Yes --> LOOP_SVC
    SVC_CHECK -- No  --> LOOP_SUB

    subgraph LOOP_SVC["For each worker group — microservice path"]
        direction TB
        BUILD_PAYLOAD["Build provision payload\nroute + dest templates"] --> POST_SVC["POST /api/v1/m/{wg}/provision\nto cribl_service :8001"]
        POST_SVC --> COLLECT_SVC["Collect result"]
    end

    subgraph LOOP_SUB["For each worker group — subprocess fallback"]
        direction TB
        BUILD_CMD["Build cribl-pusher.py\nsubprocess command"] --> RUN["Run subprocess\ncapture stdout + exit code"]
        RUN --> COLLECT["Append output"]
    end

    LOOP_SVC --> RC{Exit code = 0\nand not dry_run\nand request_id?}
    LOOP_SUB --> RC
    RC -- Yes --> UPDATE["portal_update_status_internal\n→ ES _update_by_query\nterm: request_id.keyword\nparams: status=done"]
    RC -- No --> SKIP_UPDATE[Skip portal update]
    UPDATE --> RESPOND
    SKIP_UPDATE --> RESPOND["Return JSON:\noutput, returncode,\nresults, portal_update"]
```

---

## Service Catalog Flow (/catalog)

```mermaid
flowchart TD
    START([Admin/User opens /cribl/catalog]) --> AUTH{login_required}
    AUTH -- No --> LOGIN_PAGE[Redirect to /cribl/login]
    AUTH -- Yes --> RENDER[Render catalog.html]

    RENDER --> FETCH["GET /cribl/api/catalog\n(JS fetch on page load)"]
    FETCH --> CACHE{Cache valid?\n< 60s old}
    CACHE -- Yes --> RETURN_CACHE["Return cached data"]
    CACHE -- No --> BUILD

    subgraph BUILD["_build_catalog()"]
        direction TB
        B1["1. ES _search\nonboarding index\nlast 500 docs\ndedup by apmid"] --> B2
        B2["2. fetch_role_mappings\nall entitlement clusters"] --> B3
        B3["3. Cribl auth + GET routes\nper workspace / worker group"] --> B4
        B4["4. ILM explain\nThreadPoolExecutor\n10 workers in parallel"] --> B5
        B5["5. Assemble per-app records\nroutes count + ILM tier\nrole count + status"]
    end

    BUILD --> STORE["Store in _catalog_cache\nwith timestamp"]
    STORE --> RETURN_CACHE

    RETURN_CACHE --> TABLE["Render paginated table\nSearch + filter + sort\nStats bar"]

    TABLE --> ADMIN_ACTION{IS_ADMIN?}
    ADMIN_ACTION -- Yes --> OFFBOARD_BTN["Show Offboard + Re-onboard buttons"]
    ADMIN_ACTION -- No --> VIEW_ONLY["View only"]

    OFFBOARD_BTN --> OFFBOARD["DELETE /cribl/api/catalog/{apm_id}\n?dry_run=true|false"]
    OFFBOARD --> OFFBOARD_STEPS["1. Remove Cribl routes\n   (PATCH route table)\n2. Delete ELK role_mappings\n   (BasicAuth or ApiKey)\n3. Delete ELK roles\n4. ES _update_by_query\n   apmid.keyword = apm_id\n   status = offboarded"]
    OFFBOARD_STEPS --> BUST["Bust catalog cache\n(skip on dry_run)"]

    OFFBOARD_BTN --> REONBOARD["POST /cribl/run\n{apmid, appname, region, worker_group}"]
    REONBOARD --> CRIBL_SVC{CRIBL_SERVICE_URL?}
    CRIBL_SVC -- Yes --> POST_PROV["POST provision\nto cribl_service"]
    CRIBL_SVC -- No --> SUBPROCESS["Run cribl-pusher.py\nsubprocess"]
```

---

## role_rm.py Flow

```mermaid
flowchart TD
    START([Start]) --> ARGS[Parse CLI arguments]

    ARGS --> APPS{--from-file?}
    APPS -- Yes --> FILE[read_apps_from_file\nappids.txt or --appfile]
    APPS -- No  --> SINGLE["apps = [(app_name, apmid)]"]
    FILE    --> VAL
    SINGLE  --> VAL

    subgraph VAL[Validate]
        direction TB
        V2[ELK Nonprod URL + creds required]
        V4[ELK Prod URL + creds required]
        V7[Workspace required]
        V1[All skipped if --skip-elk / --skip-cribl]
    end

    VAL --> VALERR{Errors?}
    VALERR -- Yes --> DIE1([Exit with error])
    VALERR -- No  --> TMPL

    TMPL["save_templates (always runs)\nWrite 4 JSON files per app →\nops_rm_r_templates_output/"] --> CONFIRM

    CONFIRM{"--yes or\n--dry-run?"} -- No  --> PROMPT[Prompt: type YES]
    CONFIRM -- Yes --> SESSIONS
    PROMPT --> PCONF{Confirmed?}
    PCONF -- No  --> DIE2([Exit: aborted])
    PCONF -- Yes --> SESSIONS

    SESSIONS["Build ELK sessions\nNonprod + Prod"] --> ORDER

    ORDER{--order} -- elk-first   --> ELK
    ORDER          -- cribl-first --> CRIBL2

    subgraph ELK[run_elk]
        direction TB
        ES{--skip-elk?}
        ES -- Yes --> ELKSKIP([ELK skipped])
        ES -- No  --> ELKLOOP

        subgraph ELKLOOP["For each app x 4 configs"]
            direction TB
            ENVCHECK{"environment\n== prod?"}
            ENVCHECK -- Yes --> USEPROD[Prod URL + session]
            ENVCHECK -- No  --> USENP[Nonprod URL + session]
            USEPROD --> GEN
            USENP   --> GEN
            GEN[generate_templates\nPUSER + USER\nrole + role_mapping] --> DR1{--dry-run?}
            DR1 -- Yes --> DRL1[Log DRY-RUN]
            DR1 -- No  --> ELPUT[PUT role + role_mapping\nx4 per app]
            ELPUT --> PUTRES{200/201?}
            PUTRES -- Yes --> PUTOK[Log OK]
            PUTRES -- No  --> PUTERR[Log ERR]
        end
    end

    subgraph CRIBL[run_cribl]
        direction TB
        CS{--skip-cribl?}
        CS -- Yes --> CRSKIP([Cribl skipped])
        CS -- No  --> LOADCFG[Load config + workspace]
        LOADCFG --> AUTH{token?}
        AUTH -- No  --> LOGIN[POST /api/v1/auth/login]
        AUTH -- Yes --> GR
        LOGIN --> GR
        GR["GET /routes + GET /outputs"] --> SMIN{"total_routes\n>= min_routes?"}
        SMIN -- No  --> DIED3([Exit: safety check])
        SMIN -- Yes --> BUILD["Build new routes + dests\nSkip duplicates"]
        BUILD --> SAFTER{"total_after\n>= total_before?"}
        SAFTER -- No  --> DIED4([Exit: safety check])
        SAFTER -- Yes --> DR2{--dry-run?}
        DR2 -- Yes --> DRL2[Log DRY-RUN]
        DR2 -- No  --> SNAP[Write snapshot] --> POST_D[POST new dests]
        POST_D --> PATCH["PATCH routes"]
        PATCH --> PLOG[Log OK + rollback path]
    end

    ELK    --> CRIBL
    CRIBL2 --> CRIBL3[run_cribl]
    CRIBL3 --> ELK2[run_elk]
    ELK2   --> DONE

    CRIBL  --> DONE([Done])
```

---

## cribl_service — Request Lifecycle

> How a single API call flows through cribl_service internally.

```mermaid
flowchart TD
    REQ([Incoming HTTP request\nfrom cribl-framework]) --> ROUTER["FastAPI router\nSync (deps.py CriblClient) or\nAsync (cribl_client.py AsyncCriblClient)"]
    ROUTER --> DEP{Depends type}

    DEP -- "get_cribl_client\n(sync routes)" --> SYNC_AUTH
    DEP -- "get_async_cribl_client\n(stream/edge/workgroups)" --> ASYNC_AUTH

    subgraph SYNC_AUTH["Sync auth (requests)"]
        direction TB
        S1{CRIBL_TOKEN?} -- Yes --> S2["Bearer token from env"]
        S1 -- No --> S3["POST /api/v1/auth/login\n→ token"]
    end

    subgraph ASYNC_AUTH["Async auth (httpx)"]
        direction TB
        A1{CRIBL_TOKEN?} -- Yes --> A2["Bearer token from env"]
        A1 -- No --> A3["POST /api/v1/auth/login\nasync → token"]
    end

    SYNC_AUTH --> METHOD
    ASYNC_AUTH --> METHOD

    METHOD{Which method?}

    METHOD -- "GET/PATCH routes" --> GET_R["GET /api/v1/m/{wg}/routes/{table}\n→ return raw Cribl response"]
    METHOD -- "Upsert route\n(async)" --> UPS_R["GET routes → unwrap\n→ idempotency check\n→ safety gate (min_routes)\n→ insert above final:true\n→ save snapshot\n→ PATCH routes back"]
    METHOD -- "Delete route" --> DEL_R["GET routes → filter\n→ PATCH routes back\n→ 404 if not found"]
    METHOD -- "CRUD outputs" --> DEST["GET/POST/PATCH/DELETE\n/api/v1/m/{wg}/system/outputs"]
    METHOD -- "CRUD pipelines" --> PIPE["GET/POST/PATCH/DELETE\n/api/v1/m/{wg}/pipelines"]
    METHOD -- "Worker groups\n(sync)" --> WG["GET/POST/PATCH/DELETE\n/api/v1/master/groups"]
    METHOD -- "Workgroups\n(async)" --> AWG["GET/POST/PATCH/DELETE\n/api/v1/master/groups"]
    METHOD -- "Edge fleets\n(async)" --> FLEET["GET/POST/PATCH/DELETE\n/api/v1/fleet"]
    METHOD -- "Leaders" --> LD["GET info / git-settings\nPATCH git-settings"]
    METHOD -- "Provision\n(sync)" --> PROV["Bulk route+dest upsert\nidempotent per worker group"]

    GET_R --> RESP
    UPS_R --> RESP
    DEL_R --> RESP
    DEST  --> RESP
    PIPE  --> RESP
    WG    --> RESP
    AWG   --> RESP
    FLEET --> RESP
    LD    --> RESP
    PROV  --> RESP

    RESP{Cribl returned\n>= 400?}
    RESP -- Yes --> ERR["Raise HTTPException\nstatus=Cribl status\ndetail=Cribl body[:400]"]
    RESP -- No  --> OK["Return JSON to caller"]
```

---

## ece_service — Request Lifecycle

```mermaid
flowchart TD
    REQ([Incoming HTTP request]) --> ROUTER["FastAPI router\nSync (deps.py ECEClient) or\nAsync (ece_client.py AsyncECEClient)"]

    ROUTER --> DEP{Depends type}
    DEP -- "get_ece_client\n(sync — roles, role-mappings,\nindexes, logstash, kibana)" --> SYNC_ECE
    DEP -- "get_async_ece_client\n(async — ILM policies)" --> ASYNC_ECE

    subgraph SYNC_ECE["Sync ECEClient (requests)"]
        direction TB
        EC1["Build session + headers\n(ApiKey or Basic Auth)\nfor nonprod + prod + Kibana"]
    end

    subgraph ASYNC_ECE["AsyncECEClient (httpx)"]
        direction TB
        EA1["httpx.AsyncClient per request\nApiKey or Basic Auth\nfor ES nonprod / prod / Kibana"]
    end

    SYNC_ECE --> TARGET{"?target=\nnpd or prod"}
    ASYNC_ECE --> TARGET

    TARGET -- nonprod --> NP["ES nonprod cluster\nECE_ES_URL"]
    TARGET -- prod --> PROD["ES prod cluster\nECE_ES_URL_PROD"]

    NP --> ES_OP
    PROD --> ES_OP

    ES_OP{Operation}
    ES_OP -- "roles CRUD" --> ROLES["PUT/GET/DELETE\n/_security/role/{name}"]
    ES_OP -- "role-mappings CRUD" --> RM["PUT/GET/DELETE\n/_security/role_mapping/{name}"]
    ES_OP -- "indexes CRUD" --> IDX["GET/PUT/DELETE\n/{index}\n/_cat/indices/{pattern}"]
    ES_OP -- "ILM policies CRUD\n(async)" --> ILM["GET/PUT/DELETE\n/_ilm/policy/{name}\n/{index}/_ilm/explain"]
    ES_OP -- "logstash pipelines" --> LSP["GET/PUT/DELETE\n/_logstash/pipeline/{id}"]
    ES_OP -- "kibana dashboards" --> KIB["Kibana saved objects API\n/api/saved_objects/dashboard"]
    ES_OP -- "provision app" --> PROV["generate_templates()\npush_elk() from role_rm.py\nnpd + prod clusters"]
```

---

## Admin Status Update Flow

```mermaid
flowchart TD
    ADMIN([Admin opens /cribl/portal/admin/update-status]) --> AUTH{Admin role\nin session?}
    AUTH -- No --> DENY["403 Unauthorized"]
    AUTH -- Yes --> FORM["Enter Request ID,\nselect new Status"]
    FORM --> POST["POST /cribl/portal/admin/update-status"]
    POST --> QUERY["ES _update_by_query\nterm: request_id.keyword\nscript params: {status}"]
    QUERY --> FOUND{Documents\nupdated > 0?}
    FOUND -- No --> NOT_FOUND["404 Request ID not found"]
    FOUND -- Yes --> OK["200 + updated count"]
```

---

## Summary Table

| Step | Always runs | Description |
|------|:-----------:|-------------|
| Portal submit | on request | Client fills form, doc indexed to ES with `status=pending` |
| Parse args (CLI) | yes | Single app or bulk file |
| Validate | yes | URLs, credentials, workspace |
| Save ELK templates | yes | 4 JSON files per app in `ops_rm_r_templates_output/` |
| Confirm | yes | Auto-confirmed with `--yes` or `--dry-run` |
| `run_elk` | if not `--skip-elk` | PUT roles + role-mappings to correct cluster |
| `run_cribl` | if not `--skip-cribl` | GET → plan → snapshot → POST dests → PATCH routes |
| Auto-update status | if request_id set | ES `_update_by_query` sets `status=done` (uses `.keyword` field) |

## ELK Environment Routing

| Config block | Cluster |
|---|---|
| `test` onshore + offshore | `--elk-url` nonprod |
| `prod` onshore + offshore | `--elk-url-prod` prod |

## Cribl Safety Gates

| Gate | Prevents |
|---|---|
| `total_before >= min_routes` | Running against an empty/broken config |
| `total_after >= total_before` | Accidentally deleting existing routes |
| Duplicate name/filter check | Adding the same route twice |
| Snapshot written before write | Provides rollback point |
| `group_id` sentinel (None) | Searching only first attr key when group in second |
