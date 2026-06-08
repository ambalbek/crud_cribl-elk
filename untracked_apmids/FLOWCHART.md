# Untracked AppId Detector — Flowcharts

## 1. Main Application Flow

```mermaid
flowchart TD
    A([Start]) --> B[Load Configuration]
    B --> B1{Config file<br/>provided?}
    B1 -->|Yes| B2[Parse config.json]
    B1 -->|No| B3[Use ENV vars + defaults]
    B2 --> B4[Merge: CLI > JSON > ENV > Defaults]
    B3 --> B4

    B4 --> C{.env file<br/>specified?}
    C -->|Yes| C1[Load .env into os.environ]
    C -->|No| D
    C1 --> D

    D[Resolve Authentication Method]
    D --> D1{client_id +<br/>client_secret?}
    D1 -->|Yes| D2[OAuth2 Cloud Auth]
    D1 -->|No| D3{username +<br/>password?}
    D3 -->|Yes| D4[Leader Login Auth]
    D3 -->|No| D5{static<br/>token?}
    D5 -->|Yes| D6[Static Bearer Token]
    D5 -->|No| D7([EXIT 1: No auth])

    D2 --> E{Mode?}
    D4 --> E
    D6 --> E

    E -->|--dry-run| F[Dry Run Mode]
    E -->|--inspect| G[Inspect Mode]
    E -->|default| H[Full Analysis Mode]

    F --> F1[Validate auth + connectivity]
    F1 --> F2[Show run plan + config]
    F2 --> F3([EXIT 0])

    G --> G1[List all destinations]
    G1 --> G2[Capture sample events]
    G2 --> G3[Display fields + suggest filters]
    G3 --> G4([EXIT 0])

    H --> I[Launch ThreadPoolExecutor]
    I --> J1[Worker Group 1]
    I --> J2[Worker Group 2]
    I --> JN[Worker Group N]

    J1 --> K[Merge Results from All Groups]
    J2 --> K
    JN --> K

    K --> L[Load Lookup Table]
    L --> LA{Lookup appIds<br/>hitting default?}
    LA -->|Yes| LB[GET /routes]
    LB --> LC[Check route + dest<br/>for each lookup appId]
    LC --> LD[Print ALERT table<br/>Write lookup_status.csv]
    LD --> M[Load Previous CSV]
    LA -->|No| M
    M --> N{New unmatched<br/>appIds found?}

    N -->|Yes| O[Output Results]
    N -->|No| P([EXIT 0: Nothing new])

    O --> O1[CSV Output]
    O --> O2[JSON Output]
    O --> O3[Elasticsearch Bulk Index]
    O1 --> Q([EXIT 0])
    O2 --> Q
    O3 --> Q

    style A fill:#4CAF50,color:#fff
    style D7 fill:#f44336,color:#fff
    style F3 fill:#2196F3,color:#fff
    style G4 fill:#2196F3,color:#fff
    style LB fill:#FF5722,color:#fff
    style LD fill:#FF5722,color:#fff
    style P fill:#FF9800,color:#fff
    style Q fill:#4CAF50,color:#fff
```

---

## 2. Per-Worker-Group Capture Flow

```mermaid
flowchart TD
    A([Start Group Processing]) --> B[Round 1 of N]

    B --> C[Refresh Auth Token]
    C --> D[POST /system/capture]
    D --> D1[Filter: events to default output]
    D1 --> D2[Duration: --seconds]
    D2 --> D3[Max events: --max-events]
    D3 --> D4[Level: --level]

    D4 --> E{Response OK?}
    E -->|Yes| F[Parse NDJSON Stream]
    E -->|No| E1{Retryable?<br/>429 / 5xx}
    E1 -->|Yes| E2[Backoff + Retry<br/>up to 3 attempts]
    E2 --> D
    E1 -->|No| E3[Log Error]
    E3 --> E4([Return partial results])

    F --> G[Extract apmId from each event]
    G --> H[Count events per apmId]

    H --> I{More rounds?}
    I -->|Yes| I1[Wait --interval seconds]
    I1 --> B2[Round N of N]
    B2 --> C
    I -->|No| J[GET /system/outputs]

    J --> K[Filter to azure_blob destinations]
    K --> L[Match each appId to destinations]

    L --> M{Match found?}
    M -->|Yes| M1[Record: appId -> destination_id]
    M -->|No| M2[Record: appId -> DEFAULT]

    M1 --> N[Return group results]
    M2 --> N

    N --> O([Done])

    style A fill:#4CAF50,color:#fff
    style E4 fill:#f44336,color:#fff
    style O fill:#4CAF50,color:#fff
```

---

## 3. Authentication Flow

```mermaid
flowchart TD
    A([Auth Request]) --> B{Credentials<br/>Available?}

    B -->|client_id + secret| C[OAuth2 Flow]
    C --> C1[POST https://login.cribl.cloud/oauth/token]
    C1 --> C2{200 OK?}
    C2 -->|Yes| C3[Cache access_token + expiry]
    C2 -->|No| C4([Auth Failed])

    B -->|username + password| D[Leader Login]
    D --> D1[POST /api/v1/auth/login]
    D1 --> D2{200 OK?}
    D2 -->|Yes| D3[Cache bearer token]
    D2 -->|No| D4([Auth Failed])

    B -->|static token| E[Use Token Directly]
    E --> E1[Set Authorization header]

    C3 --> F{Token expired?}
    D3 --> F
    E1 --> G([Authenticated])

    F -->|Yes| A
    F -->|No| G

    style A fill:#2196F3,color:#fff
    style C4 fill:#f44336,color:#fff
    style D4 fill:#f44336,color:#fff
    style G fill:#4CAF50,color:#fff
```

---

## 4. Matching & Deduplication Flow

```mermaid
flowchart TD
    A([Captured AppIds]) --> B[For each appId]

    B --> C{Match Mode?}

    C -->|exact| D[containerName == appId<br/>case-insensitive]
    C -->|contains| E[appId in containerName<br/>case-insensitive]
    C -->|partition| F[exact match OR<br/>appId in partitionExpr]

    D --> G{Match?}
    E --> G
    F --> G

    G -->|Yes| H[Tracked: appId -> destination]
    G -->|No| I[Untracked: appId -> DEFAULT]

    I --> J{In lookup table?<br/>azure_storage_account_containers}
    J -->|Yes| K[Route/Dest Audit]
    J -->|No| L{In previous CSV?}

    K --> K1[GET /routes]
    K1 --> K2{Dest exists?<br/>containerName / ID / name}
    K2 -->|Yes| K3{Route exists?<br/>name / ID / filter / output}
    K2 -->|No| K4{Route exists?}
    K3 -->|Yes| K5[CONFIGURED]
    K3 -->|No| K6[MISSING ROUTE]
    K4 -->|Yes| K7[MISSING DESTINATION]
    K4 -->|No| K8[MISSING BOTH]
    K5 --> K9[Write lookup_status.csv]
    K6 --> K9
    K7 --> K9
    K8 --> K9

    L -->|Yes| M[Exclude: already reported]
    L -->|No| N[NEW untracked appId]

    N --> O[Add to output results]

    style A fill:#2196F3,color:#fff
    style H fill:#4CAF50,color:#fff
    style K fill:#FF5722,color:#fff
    style K5 fill:#4CAF50,color:#fff
    style K6 fill:#f44336,color:#fff
    style K7 fill:#f44336,color:#fff
    style K8 fill:#f44336,color:#fff
    style M fill:#FF9800,color:#fff
    style N fill:#f44336,color:#fff
    style O fill:#9C27B0,color:#fff
```

---

## 5. Package Architecture

```mermaid
flowchart LR
    CLI[cli.py<br/>Entry point + argparse] --> CONFIG[config.py<br/>JSON + .env loading]
    CLI --> AUTH[auth.py<br/>OAuth2 / Login / Token]
    CLI --> ANALYSIS[analysis.py<br/>inspect / dry-run / full]

    ANALYSIS --> CLIENT[client.py<br/>Cribl REST API]
    ANALYSIS --> MATCHING[matching.py<br/>Matching engine + audit]
    ANALYSIS --> LOOKUP[lookup.py<br/>Lookup table + CSV diff]
    ANALYSIS --> OUTPUT[output.py<br/>CSV / JSON / tables]
    ANALYSIS --> ES[elasticsearch.py<br/>Bulk indexing]

    CLIENT --> AUTH
    CLIENT --> HTTP[http.py<br/>Session + retry]
    AUTH --> HTTP
    ES --> HTTP

    HTTP --> CONST[constants.py<br/>Timeouts / exit codes]
    HTTP --> EXCEPT[exceptions.py<br/>Error types]

    style CLI fill:#2196F3,color:#fff
    style ANALYSIS fill:#9C27B0,color:#fff
    style MATCHING fill:#FF5722,color:#fff
    style CONST fill:#607D8B,color:#fff
```

---

## 6. Error Handling & Exit Codes

```mermaid
flowchart TD
    A([Execution Complete]) --> B{All groups<br/>succeeded?}

    B -->|Yes| C{Results<br/>found?}
    C -->|Yes| D[Write outputs]
    D --> E([EXIT 0: Success])
    C -->|No| E

    B -->|No| F{Any group<br/>succeeded?}
    F -->|Yes| G[Write partial results]
    G --> H([EXIT 2: Partial])
    F -->|No| I([EXIT 1: Fatal])

    J{Ctrl+C<br/>received?} -->|Yes| K[Save partial results]
    K --> L([EXIT 130: Interrupted])

    style E fill:#4CAF50,color:#fff
    style H fill:#FF9800,color:#fff
    style I fill:#f44336,color:#fff
    style L fill:#9C27B0,color:#fff
```

---

## How to Render

These diagrams use [Mermaid](https://mermaid.js.org/) syntax. They render natively in:
- GitHub / GitLab Markdown viewers
- VS Code with the Mermaid extension
- [Mermaid Live Editor](https://mermaid.live)
- Confluence with Mermaid macro
- Jira with Mermaid add-on
