# Cribl Framework — Python Files Flowchart

## 1. Module Dependency Graph

```mermaid
flowchart TD
    APP["app.py<br><i>Flask Web UI :5000</i><br>Auth, Portal, Pusher UI,<br>Entitlements, Admin"]

    APP -- "subprocess.run()" --> PUSHER["cribl-pusher.py<br><i>CLI Script</i><br>Routes + Destinations"]
    APP -- "subprocess.run()" --> RODE["role_rm.py<br><i>CLI Script</i><br>ELK Roles + Cribl Routes"]

    PUSHER -- import --> CONFIG["cribl_config.py<br><i>Library</i><br>Config loading, URL building,<br>credential resolution"]
    PUSHER -- import --> API["cribl_api.py<br><i>Library</i><br>Auth, route normalization,<br>group management"]
    PUSHER -- import --> UTILS["cribl_utils.py<br><i>Library</i><br>Prompts, file I/O, diffs,<br>sessions, error handling"]
    PUSHER -- import --> LOGGER["cribl_logger.py<br><i>Library</i><br>Logging setup"]

    RODE -- import --> CONFIG
    RODE -- import --> API
    RODE -- import --> UTILS

    CONFIG -- import --> UTILS
    API -- import --> UTILS

    VALIDATE["_validate.py<br><i>Test Script</i><br>Offline validation"] -- import --> CONFIG
    VALIDATE -- import --> API
    VALIDATE -- import --> UTILS
    VALIDATE -- import --> LOGGER

    ENT["entitlement/app.py<br><i>Standalone Flask :8282</i><br>ELK entitlement viewer"]

    style APP fill:#0284c7,color:#fff
    style PUSHER fill:#e53e3e,color:#fff
    style RODE fill:#e53e3e,color:#fff
    style CONFIG fill:#64748b,color:#fff
    style API fill:#64748b,color:#fff
    style UTILS fill:#64748b,color:#fff
    style LOGGER fill:#64748b,color:#fff
    style VALIDATE fill:#22c55e,color:#fff
    style ENT fill:#8b5cf6,color:#fff
```

---

## 2. File Purpose Summary

```mermaid
flowchart LR
    subgraph WEB["Web Applications"]
        APP["app.py<br>Flask :5000<br>Main entry point"]
        ENT["entitlement/app.py<br>Flask :8282<br>Standalone viewer"]
    end

    subgraph CLI["CLI Scripts"]
        PUSHER["cribl-pusher.py<br>Routes + Destinations"]
        RODE["role_rm.py<br>ELK Roles + Cribl"]
    end

    subgraph LIB["Shared Libraries"]
        CONFIG["cribl_config.py<br>Config + URLs"]
        API["cribl_api.py<br>Cribl API"]
        UTILS["cribl_utils.py<br>Utilities"]
        LOGGER["cribl_logger.py<br>Logging"]
    end

    subgraph TEST["Testing"]
        VAL["_validate.py<br>Offline validation"]
    end

    style WEB fill:#0284c7,color:#fff
    style CLI fill:#e53e3e,color:#fff
    style LIB fill:#64748b,color:#fff
    style TEST fill:#22c55e,color:#fff
```

---

## 3. Onboarding Request Lifecycle

```mermaid
flowchart TD
    CLIENT([Client logs in]) --> PORTAL["/cribl/portal — Onboarding Form"]
    PORTAL --> SUBMIT["POST /cribl/portal/api/submit<br><b>app.py</b>"]
    SUBMIT --> VALIDATE{Validation OK?}
    VALIDATE -- No --> ERRORS["Return 400 + errors"]
    VALIDATE -- Yes --> INDEX["es_index()<br>Write to Elasticsearch"]
    INDEX --> PENDING[("ES Document<br>status = pending<br>REQ-YYYYMMDD-XX")]

    ADMIN([Admin logs in]) --> PUSHER_UI["/cribl/app — Pusher UI"]
    PUSHER_UI --> PASTE["Paste Request ID<br>Select workspace + worker groups"]
    PASTE --> DRYRUN{Dry Run?}
    DRYRUN -- Yes --> PREVIEW["Preview diff<br>No writes"]
    PREVIEW --> PASTE
    DRYRUN -- No --> SUBPROCESS["subprocess.run()<br><b>app.py</b>"]

    SUBPROCESS --> CRIBL_PUSHER["<b>cribl-pusher.py</b>"]
    CRIBL_PUSHER --> POST_DEST["POST destinations<br>to Cribl Stream"]
    POST_DEST --> PATCH_ROUTES["PATCH routes<br>to Cribl Stream"]
    PATCH_ROUTES --> UPDATE_STATUS["portal_update_status()<br><b>app.py</b>"]
    UPDATE_STATUS --> DONE[("ES Document<br>status = done")]

    style CLIENT fill:#22c55e,color:#fff
    style ADMIN fill:#22c55e,color:#fff
    style PENDING fill:#f97316,color:#fff
    style DONE fill:#22c55e,color:#fff
```

---

## 4. ELK Roles + Cribl Routes (role_rm.py)

```mermaid
flowchart TD
    ADMIN([Admin runs role_rm]) --> PARSE["Parse args<br>Load apps from file or CLI"]
    PARSE --> TEMPLATES["generate_templates()<br>Render role + role-mapping<br>via Jinja2"]
    TEMPLATES --> SAVE["save_templates()<br>Write to ops_rm_r_templates_output/"]

    SAVE --> ORDER{--order?}
    ORDER -- "elk-first<br>(default)" --> ELK_FIRST
    ORDER -- "cribl-first" --> CRIBL_FIRST

    subgraph ELK_FIRST[" "]
        direction TB
        ELK1["push_elk()"] --> CRIBL1["push_cribl()"]
    end

    subgraph CRIBL_FIRST[" "]
        direction TB
        CRIBL2["push_cribl()"] --> ELK2["push_elk()"]
    end

    ELK_FIRST --> DONE_EF([Done])
    CRIBL_FIRST --> DONE_CF([Done])

    subgraph PUSH_ELK["push_elk() detail"]
        direction TB
        SKIP_ELK{--skip-elk?}
        SKIP_ELK -- Yes --> ELKSKIP([Skipped])
        SKIP_ELK -- No --> ELKLOOP["For each app x 4 configs"]
        ELKLOOP --> DRY_ELK{--dry-run?}
        DRY_ELK -- Yes --> LOG_DRY["Log DRY-RUN"]
        DRY_ELK -- No --> PUT_ROLE["PUT /_security/role<br>PUT /_security/role_mapping"]
    end

    subgraph PUSH_CRIBL["push_cribl() detail"]
        direction TB
        SKIP_CRIBL{--skip-cribl?}
        SKIP_CRIBL -- Yes --> CRSKIP([Skipped])
        SKIP_CRIBL -- No --> GET_ROUTES["GET /routes + /outputs"]
        GET_ROUTES --> SAFETY{Safety checks pass?}
        SAFETY -- No --> EXIT_SAFE([Exit: safety])
        SAFETY -- Yes --> DRY_CR{--dry-run?}
        DRY_CR -- Yes --> LOG_DRY2["Log DRY-RUN"]
        DRY_CR -- No --> SNAPSHOT["Save snapshot"]
        SNAPSHOT --> POST_DEST["POST destinations"]
        POST_DEST --> PATCH_RT["PATCH routes"]
    end

    style ADMIN fill:#22c55e,color:#fff
```

---

## 5. Entitlement Lookup Flow

```mermaid
flowchart TD
    USER([User or Admin opens /cribl/entitlements]) --> FETCH["GET /cribl/api/entitlements<br><b>app.py</b>"]
    FETCH --> LOAD_CFG["Load entitlement config<br>from config.json"]
    LOAD_CFG --> LOOP

    subgraph LOOP["For each ES cluster"]
        direction TB
        FETCH_RM["fetch_role_mappings(cluster)<br>GET /_security/role_mapping<br>HTTPBasicAuth"] --> EXTRACT["extract_entitlement_cns()<br>Walk rules tree<br>Match filter text"]
        EXTRACT --> PARSE_CN["parse_cn()<br>Extract CN from DN"]
        PARSE_CN --> BUILD["Build result object:<br>cluster, mappingName,<br>entitlement, entitlementDN,<br>roles, enabled"]
        BUILD --> ERR{Error?}
        ERR -- Yes --> ERR_REC["Add error record<br>error: true"]
        ERR -- No --> NEXT([Next cluster])
    end

    LOOP --> SORT["Sort by cluster + entitlement"]
    SORT --> JSON["Return JSON array"]
    JSON --> RENDER["Browser renders table<br>Search, filter, sort,<br>pagination, CSV export"]

    style USER fill:#22c55e,color:#fff
    style ERR_REC fill:#e53e3e,color:#fff
```

---

## 6. Authentication Flow

```mermaid
flowchart TD
    USER([User visits any page]) --> CHECK{Session valid?}
    CHECK -- Yes --> ALLOW["Allow access<br>Page renders"]
    CHECK -- No --> REDIRECT["Redirect to /cribl/login"]
    REDIRECT --> FORM["Show login form<br>Username + Password"]
    FORM --> POST["POST /cribl/login"]
    POST --> AUTH["local_authenticate()<br><b>app.py</b>"]

    AUTH --> ADMINS{"Match<br>local_admins?"}
    ADMINS -- Yes --> ROLE_ADMIN["role = admin"]
    ADMINS -- No --> USERS{"Match<br>local_users?"}
    USERS -- Yes --> ROLE_USER["role = user"]
    USERS -- No --> FAIL["Show error:<br>Invalid credentials"]
    FAIL --> FORM

    ROLE_ADMIN --> SESSION["Create session<br>username, role, display_name"]
    ROLE_USER --> SESSION

    SESSION --> ROLE_CHECK{Role?}
    ROLE_CHECK -- "admin" --> ALL["Redirect to requested page<br>Full access"]
    ROLE_CHECK -- "user" --> PORTAL["Redirect to /cribl/portal<br>Portal + Entitlements only"]

    ALL --> DONE([Authenticated])
    PORTAL --> DONE

    style USER fill:#22c55e,color:#fff
    style DONE fill:#22c55e,color:#fff
    style FAIL fill:#e53e3e,color:#fff
    style ROLE_ADMIN fill:#0284c7,color:#fff
    style ROLE_USER fill:#f59e0b,color:#fff
```

---

## 7. cribl-pusher.py Internal Flow

```mermaid
flowchart TD
    START([Start]) --> ARGS["Parse CLI args<br>build_parser()"]
    ARGS --> LOG["setup_logging()<br><b>cribl_logger.py</b>"]
    LOG --> CFG["load_config()<br><b>cribl_config.py</b>"]
    CFG --> WS["Resolve workspace + worker group<br>get_workspace()<br><b>cribl_config.py</b>"]
    WS --> CREDS["resolve_credentials()<br>CLI > env vars > config.json<br><b>cribl_config.py</b>"]
    CREDS --> TMPL["Load route + dest templates<br>get_route_template_path()<br>get_dest_template_path()<br><b>cribl_config.py</b>"]
    TMPL --> SESSION["make_session()<br><b>cribl_utils.py</b>"]
    SESSION --> AUTH["cribl_login_token()<br><b>cribl_api.py</b>"]
    AUTH --> GET_R["GET /routes/{table}<br>unwrap_response()<br><b>cribl_api.py</b>"]
    GET_R --> GET_O["GET /system/outputs<br>Build skip list"]
    GET_O --> SAFETY1{"total_routes >=<br>min_routes?"}
    SAFETY1 -- No --> DIE1([EXIT: safety check])
    SAFETY1 -- Yes --> BUILD["Build new routes<br>normalize_route()<br>find_default_route_index()<br><b>cribl_api.py</b><br>Skip duplicates"]
    BUILD --> SAFETY2{"total_after >=<br>total_before?"}
    SAFETY2 -- No --> DIE2([EXIT: would lose routes])
    SAFETY2 -- Yes --> DIFF["unified_diff()<br><b>cribl_utils.py</b>"]
    DIFF --> CONFIRM["confirm_or_exit()<br><b>cribl_utils.py</b>"]
    CONFIRM --> DRY{--dry-run?}
    DRY -- Yes --> DRY_LOG["Log DRY-RUN<br>Show diff"]
    DRY -- No --> SNAP["Save snapshot JSON"]
    SNAP --> POST_D["POST new destinations"]
    POST_D --> PATCH["PATCH routes to Cribl"]
    PATCH --> DONE([Done])

    style START fill:#22c55e,color:#fff
    style DONE fill:#22c55e,color:#fff
    style DIE1 fill:#e53e3e,color:#fff
    style DIE2 fill:#e53e3e,color:#fff
```

---

## 8. Application Architecture

```mermaid
flowchart TD
    BROWSER["Browser"]

    subgraph FRAMEWORK["Cribl Framework (Flask :5000)"]
        direction TB
        LOGIN["/cribl/login"]
        PORTAL["/cribl/portal"]
        PUSHER_UI["/cribl/app"]
        ENTITLE["/cribl/entitlements"]
        ADMIN_UI["/cribl/portal/admin"]
        HEALTH["/cribl/health"]
    end

    BROWSER --> FRAMEWORK

    subgraph EXTERNAL["External Systems"]
        direction LR
        ES[("Elasticsearch<br>Clusters")]
        CRIBL[("Cribl Stream<br>API")]
        CFG["config.json"]
    end

    FRAMEWORK -- "Onboarding docs<br>Role mappings<br>Status updates" --> ES
    FRAMEWORK -- "Routes<br>Destinations" --> CRIBL
    FRAMEWORK -. "Read config" .-> CFG

    subgraph SCRIPTS["CLI Scripts (subprocess)"]
        direction LR
        CP["cribl-pusher.py"]
        RM["role_rm.py"]
    end

    FRAMEWORK -- "subprocess.run()" --> SCRIPTS
    SCRIPTS -- "API calls" --> ES
    SCRIPTS -- "API calls" --> CRIBL

    subgraph LIBS["Shared Libraries"]
        direction LR
        L1["cribl_config.py"]
        L2["cribl_api.py"]
        L3["cribl_utils.py"]
        L4["cribl_logger.py"]
    end

    SCRIPTS -- "import" --> LIBS

    STANDALONE["entitlement/app.py<br>Standalone :8282"] -- "Role mappings" --> ES

    style BROWSER fill:#94a3b8,color:#fff
    style FRAMEWORK fill:#0284c7,color:#fff
    style SCRIPTS fill:#e53e3e,color:#fff
    style LIBS fill:#64748b,color:#fff
    style STANDALONE fill:#8b5cf6,color:#fff
    style ES fill:#f97316,color:#fff
    style CRIBL fill:#e53e3e,color:#fff
```

---

## 9. Credential Resolution Order

```mermaid
flowchart TD
    START([Resolve credentials]) --> CLI{"CLI args set?<br>--token / --username / --password"}
    CLI -- Yes --> USE_CLI["Use CLI args"]
    CLI -- No --> ENV{"Env vars set?<br>CRIBL_TOKEN / CRIBL_USERNAME<br>CRIBL_PASSWORD"}
    ENV -- Yes --> USE_ENV["Use environment variables"]
    ENV -- No --> CFG{"config.json<br>credentials block set?"}
    CFG -- Yes --> USE_CFG["Use config.json values"]
    CFG -- No --> PROMPT["Interactive prompt"]

    style START fill:#22c55e,color:#fff
    style USE_CLI fill:#0284c7,color:#fff
    style USE_ENV fill:#0284c7,color:#fff
    style USE_CFG fill:#0284c7,color:#fff
    style PROMPT fill:#f59e0b,color:#fff
```

---

## 10. Safety Checks

```mermaid
flowchart TD
    GET["GET /routes from Cribl"] --> S1{"total_before >=<br>min_existing_total_routes?"}
    S1 -- No --> FAIL1["EXIT: Empty or broken config<br>Refuses to PATCH"]
    S1 -- Yes --> BUILD["Build new routes<br>Skip duplicates by name/filter"]
    BUILD --> S2{"total_after >=<br>total_before?"}
    S2 -- No --> FAIL2["EXIT: Would lose routes<br>Refuses to PATCH"]
    S2 -- Yes --> DRY{--dry-run?}
    DRY -- Yes --> PREVIEW["Show unified diff<br>No writes performed"]
    DRY -- No --> SNAPSHOT["Write snapshot<br>for rollback"]
    SNAPSHOT --> WRITE["POST destinations<br>PATCH routes"]
    WRITE --> OK([Success])

    style FAIL1 fill:#e53e3e,color:#fff
    style FAIL2 fill:#e53e3e,color:#fff
    style OK fill:#22c55e,color:#fff
    style PREVIEW fill:#f59e0b,color:#fff
```
