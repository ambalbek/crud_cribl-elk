# Cribl GitOps — Mermaid Diagrams

All architecture diagrams for the Cribl GitOps full-automation design. Each diagram is standalone and can be exported individually with mermaid-cli (`mmdc -i file.md -o file.png`).

---

## 1. Current State

```mermaid
flowchart LR
    ENG[Engineer]
    LEADER[Cribl Leader VM<br/>UI writable<br/>manages 4 envs]
    NFS[(NFS Local Git<br/>main branch only<br/>backup only)]
    WORKERS[16 Worker Groups]
    FRAMEWORK[cribl-framework<br/>on ARO]

    ENG -->|edits in UI| LEADER
    LEADER -->|commit + push| NFS
    LEADER -.->|10s poll| WORKERS
    FRAMEWORK -->|direct API writes<br/>no review| LEADER

    style LEADER fill:#7f1d1d,color:#fff
    style NFS fill:#78350f,color:#fff
    style FRAMEWORK fill:#7f1d1d,color:#fff
```

---

## 2. Target State

```mermaid
flowchart LR
    ENG[Engineer]
    FRAMEWORK[cribl-framework]
    GH[(Corporate GitHub<br/>cribl-config repo<br/>single main branch)]
    HARNESS[Harness CI/CD]
    LB[Regional Load Balancers]
    L1[Waukegan Leader<br/>UI READ-ONLY]
    L2[Fort Worth Leader<br/>UI READ-ONLY]
    L3[Azure North Leader<br/>UI READ-ONLY]
    L4[Azure South Leader<br/>UI READ-ONLY]
    W1[16 Worker Groups]
    W2[16 Worker Groups]
    W3[16 Worker Groups]
    W4[16 Worker Groups]

    ENG -->|opens PR| GH
    FRAMEWORK -->|opens PR via<br/>GitHub API| GH
    GH -->|webhook on merge| HARNESS
    HARNESS -->|POST /version/sync<br/>parallel matrix| LB
    LB --> L1
    LB --> L2
    LB --> L3
    LB --> L4
    L1 -.->|10s poll| W1
    L2 -.->|10s poll| W2
    L3 -.->|10s poll| W3
    L4 -.->|10s poll| W4

    style GH fill:#1e3a5f,color:#fff
    style HARNESS fill:#0b4f6c,color:#fff
    style L1 fill:#2d6a4f,color:#fff
    style L2 fill:#2d6a4f,color:#fff
    style L3 fill:#2d6a4f,color:#fff
    style L4 fill:#2d6a4f,color:#fff
```

---

## 3. Topology Constraint — One Leader, All Envs

```mermaid
flowchart TB
    subgraph WAUKEGAN["Region: Waukegan"]
        WAU_HA["HA Leader Pair (1 pair)<br/>manages ALL envs"]

        subgraph WG_WAU["16 Worker Groups"]
            WAU_DEV["dev-aro<br/>dev-filebeat<br/>dev-syslog<br/>dev-wec"]
            WAU_TEST["test-aro<br/>test-filebeat<br/>test-syslog<br/>test-wec"]
            WAU_ALT["altprod-aro<br/>altprod-filebeat<br/>altprod-syslog<br/>altprod-wec"]
            WAU_PROD["prod-aro<br/>prod-filebeat<br/>prod-syslog<br/>prod-wec"]
        end

        WAU_HA --> WAU_DEV
        WAU_HA --> WAU_TEST
        WAU_HA --> WAU_ALT
        WAU_HA --> WAU_PROD
    end

    subgraph FTW["Fort Worth"]
        FTW_HA["HA Leader Pair"]
        FTW_WG["16 Worker Groups<br/>(4 envs × 4 types)"]
        FTW_HA --> FTW_WG
    end

    subgraph AZN["Azure North"]
        AZN_HA["HA Leader Pair"]
        AZN_WG["16 Worker Groups"]
        AZN_HA --> AZN_WG
    end

    subgraph AZS["Azure South"]
        AZS_HA["HA Leader Pair"]
        AZS_WG["16 Worker Groups"]
        AZS_HA --> AZS_WG
    end

    style WAU_HA fill:#2d6a4f,color:#fff
    style FTW_HA fill:#2d6a4f,color:#fff
    style AZN_HA fill:#2d6a4f,color:#fff
    style AZS_HA fill:#2d6a4f,color:#fff
    style WAU_DEV fill:#3f3f46,color:#fff
    style WAU_TEST fill:#1e3a5f,color:#fff
    style WAU_ALT fill:#78350f,color:#fff
    style WAU_PROD fill:#7f1d1d,color:#fff
```

---

## 4. Core GitOps Flow

```mermaid
flowchart TD
    START([Engineer or Framework<br/>needs a config change]) --> SOURCE{Who initiates?}

    SOURCE -->|Engineer| ENG_FLOW[Engineer writes YAML<br/>or prototypes in sandbox]
    SOURCE -->|cribl-framework| FW_FLOW[Framework generates YAML<br/>from onboarding form]

    ENG_FLOW --> PR[Open PR against main<br/>modifies groups/dev-*/]
    FW_FLOW --> PR

    PR --> CHECKS[GitHub Actions:<br/>YAML lint<br/>Cribl schema validation<br/>Secret scan<br/>Duplicate check]

    CHECKS --> REVIEW{CODEOWNERS<br/>approval}
    REVIEW -->|dev| LIGHT[1 reviewer]
    REVIEW -->|test| PEER[1 peer reviewer]
    REVIEW -->|altprod| LEAD[Cribl lead]
    REVIEW -->|prod| CAB[Lead + Security<br/>+ CAB ticket]

    LIGHT --> MERGE[Merge to main]
    PEER --> MERGE
    LEAD --> MERGE
    CAB --> MERGE

    MERGE --> WEBHOOK[GitHub webhook<br/>triggers Harness]
    WEBHOOK --> DETECT[Harness detects<br/>changed env+WG folders]

    DETECT --> CANARY{Prod folders<br/>changed?}
    CANARY -->|yes| CANARY_DEPLOY[Sync 1 region first<br/>15-min soak<br/>metrics check]
    CANARY -->|no| FULL_DEPLOY

    CANARY_DEPLOY --> METRICS{Within SLO?}
    METRICS -->|yes| FULL_DEPLOY[Parallel sync<br/>to all 4 regions<br/>POST /version/sync]
    METRICS -->|no| ROLLBACK[Auto-rollback:<br/>git revert HEAD]

    FULL_DEPLOY --> VERIFY[Verify all leaders<br/>report new SHA]
    VERIFY --> NOTIFY[Slack notification<br/>+ change ticket update]

    ROLLBACK --> PAGE[Page on-call SRE]

    style PR fill:#1e3a5f,color:#fff
    style CHECKS fill:#0b4f6c,color:#fff
    style CAB fill:#7f1d1d,color:#fff
    style CANARY_DEPLOY fill:#78350f,color:#fff
    style FULL_DEPLOY fill:#2d6a4f,color:#fff
    style ROLLBACK fill:#991b1b,color:#fff
```

---

## 5. Folder-Based Promotion Model

```mermaid
flowchart LR
    DEV["groups/dev-aro/"]
    TEST["groups/test-aro/"]
    ALT["groups/altprod-aro/"]
    PROD["groups/prod-aro/"]

    DEV -->|"./promote.sh dev test aro<br/>PR + 1 reviewer"| TEST
    TEST -->|"./promote.sh test altprod aro<br/>PR + lead approval"| ALT
    ALT -->|"./promote.sh altprod prod aro<br/>PR + CAB + canary"| PROD

    style DEV fill:#3f3f46,color:#fff
    style TEST fill:#1e3a5f,color:#fff
    style ALT fill:#78350f,color:#fff
    style PROD fill:#7f1d1d,color:#fff
```

---

## 6. Change Type A — Daily Pipeline / Route / Source / Destination

```mermaid
flowchart TD
    E[Engineer needs new Route<br/>for application X]
    SANDBOX[Prototype in<br/>sandbox Cribl VM]
    YAML[Write YAML or copy<br/>from sandbox output]
    PR1[PR #1: modifies<br/>groups/dev-aro/.../routes.yml]
    CI1[CI: lint + schema + secrets]
    MERGE1[Merge to main]
    SYNC1[Harness syncs all 4 regions<br/>only dev-aro redeploys]
    VALIDATE[Validate in dev-aro WG]
    PROMOTE1[./scripts/promote.sh dev test aro]
    PR2[Auto-generated PR #2:<br/>dev-aro to test-aro]
    MERGE2[Peer review + merge]
    SYNC2[Harness syncs test-aro]
    PROMOTE2[./scripts/promote.sh test altprod aro]
    PR3[Auto-PR #3:<br/>test-aro to altprod-aro]
    MERGE3[Lead review + merge]
    SYNC3[Harness syncs altprod-aro]
    PROMOTE3[./scripts/promote.sh altprod prod aro]
    PR4[Auto-PR #4:<br/>altprod-aro to prod-aro]
    CAB[CAB approval<br/>+ change ticket]
    MERGE4[Merge]
    CANARY[Canary: 1 region<br/>15-min soak]
    ROLLOUT[Parallel rollout<br/>to remaining 3 regions]
    DONE([Production deployment complete])

    E --> SANDBOX --> YAML --> PR1 --> CI1 --> MERGE1 --> SYNC1 --> VALIDATE
    VALIDATE --> PROMOTE1 --> PR2 --> MERGE2 --> SYNC2
    SYNC2 --> PROMOTE2 --> PR3 --> MERGE3 --> SYNC3
    SYNC3 --> PROMOTE3 --> PR4 --> CAB --> MERGE4 --> CANARY --> ROLLOUT --> DONE

    style SANDBOX fill:#1e3a5f,color:#fff
    style CAB fill:#7f1d1d,color:#fff
    style CANARY fill:#78350f,color:#fff
    style DONE fill:#2d6a4f,color:#fff
```

---

## 7. Change Type B — Application Onboarding via Framework

```mermaid
flowchart TD
    APP[App team submits<br/>onboarding form]
    META[Framework collects:<br/>app name + owner<br/>data classification<br/>PII level<br/>target retention<br/>worker group type]
    VALIDATE[Framework validates:<br/>naming convention<br/>owner exists<br/>retention class<br/>no duplicates]
    GEN[Framework generates YAML:<br/>inputs.yml entry<br/>routes.yml entry<br/>pipelines/X.yml<br/>ELK role YAML]
    BRANCH[Create feature branch<br/>via GitHub API]
    COMMIT[Commit YAML files<br/>to dev-* folders only]
    PR[Open PR against main<br/>with rich metadata]
    CI[GitHub Actions:<br/>YAML lint<br/>schema validation<br/>duplicate detection]
    RISK{Standard pattern?<br/>low-risk?}
    AUTO_MERGE[Auto-merge<br/>no human review]
    QUEUE[Queue for human approval<br/>in framework UI]
    HUMAN[Human approves<br/>in framework UI]
    HARNESS[Harness syncs DEV]
    NOTIFY[Notify app team:<br/>Source live in DEV<br/>+ dashboard link]

    APP --> META --> VALIDATE --> GEN --> BRANCH --> COMMIT --> PR --> CI --> RISK
    RISK -->|yes| AUTO_MERGE
    RISK -->|no| QUEUE --> HUMAN --> AUTO_MERGE
    AUTO_MERGE --> HARNESS --> NOTIFY

    style META fill:#1e3a5f,color:#fff
    style VALIDATE fill:#0b4f6c,color:#fff
    style PR fill:#1e3a5f,color:#fff
    style AUTO_MERGE fill:#2d6a4f,color:#fff
    style NOTIFY fill:#166534,color:#fff
```

---

## 8. Change Type C — Worker Group / Infrastructure

```mermaid
flowchart TD
    REQ[Need new worker group type<br/>e.g. Kafka ingest WG]
    TF_PR[PR in cribl-infrastructure repo<br/>Terraform changes]
    TF_PLAN[Terraform plan:<br/>new VM Scale Set per region<br/>new worker group definition<br/>mapping ruleset update]
    SRE_REVIEW[SRE review of TF plan]
    TF_APPLY_DEV[Terraform apply<br/>creates dev worker group<br/>+ VM scale set]
    SKELETON[Terraform null_resource<br/>opens auto-PR in cribl-config:<br/>adds groups/dev-kafka/ skeleton]
    ENG[Engineer fills in actual config<br/>via normal PR flow Type A]
    EVENTUAL[Eventually promoted<br/>dev to test to altprod to prod<br/>via promote.sh]
    TF_PROD[Terraform applies to remaining envs<br/>creates VM scale sets<br/>in test/altprod/prod]

    REQ --> TF_PR --> TF_PLAN --> SRE_REVIEW --> TF_APPLY_DEV --> SKELETON --> ENG --> EVENTUAL --> TF_PROD

    style TF_PR fill:#1e3a5f,color:#fff
    style TF_APPLY_DEV fill:#78350f,color:#fff
    style ENG fill:#2d6a4f,color:#fff
```

---

## 9. Harness Pipeline End-to-End

```mermaid
flowchart TD
    TRIG([GitHub webhook<br/>push to main])
    S0[Stage 0: Parse webhook<br/>extract SHA + changed files]
    S1[Stage 1: Detect changes<br/>git diff HEAD~1 HEAD<br/>identify env+WG folders]
    S2[Stage 2: Pre-deploy validation<br/>YAML lint<br/>Cribl schema check<br/>gitleaks secret scan<br/>naming convention]
    S2_GATE{Validation<br/>passed?}
    FAIL_FAST[Fail pipeline<br/>auto-revert merge<br/>page on-call]
    S3{Any altprod or prod<br/>folders changed?}
    S3A[Stage 3a: Slack approval<br/>SRE lead]
    S3B[Stage 3b: CAB approval<br/>change ticket required]
    S4{Prod folders<br/>changed?}
    S4A[Stage 4a: Canary deploy<br/>1 region only]
    S4B[Stage 4b: 15-min soak<br/>monitor:<br/>worker health<br/>throughput per WG<br/>error rate<br/>queue depth<br/>destination latency]
    S4C{Within SLO?}
    RB[Auto-rollback:<br/>git revert HEAD<br/>force push<br/>re-trigger pipeline]
    S5[Stage 5: Parallel sync to all regions<br/>matrix strategy<br/>maxConcurrency: 4<br/>POST /api/v1/version/sync<br/>ref=main, deploy=true]
    S6[Stage 6: Verify<br/>GET /api/v1/master/groups<br/>all WGs report new SHA]
    S7[Stage 7: Notify<br/>Slack success<br/>update Cribl audit log<br/>close change ticket]
    PAGE[Page on-call SRE<br/>auto-create incident ticket]

    TRIG --> S0 --> S1 --> S2 --> S2_GATE
    S2_GATE -->|no| FAIL_FAST
    S2_GATE -->|yes| S3
    S3 -->|dev or test only| S5
    S3 -->|altprod| S3A --> S5
    S3 -->|prod| S3B --> S4
    S4 -->|yes| S4A --> S4B --> S4C
    S4 -->|no| S5
    S4C -->|no| RB --> PAGE
    S4C -->|yes| S5
    S5 --> S6 --> S7

    style TRIG fill:#1e3a5f,color:#fff
    style S3B fill:#7f1d1d,color:#fff
    style S4A fill:#78350f,color:#fff
    style RB fill:#991b1b,color:#fff
    style S7 fill:#2d6a4f,color:#fff
```

---

## 10. HA Leader Architecture (Per Region)

```mermaid
flowchart LR
    HARNESS[Harness CI/CD]
    GH[(GitHub<br/>source of truth)]

    subgraph REGION["Per Region (Azure VNet)"]
        LB["Azure Standard LB (NLB)<br/>Health: /health:9000 every 60s<br/>Port 9000 UI/API<br/>Port 4200 worker traffic"]

        subgraph LEADER_PAIR["Leader VMs (HA pair)"]
            L1[Leader VM 1<br/>Primary<br/>Standard_D8s_v5]
            L2[Leader VM 2<br/>Standby<br/>proxies to primary]
        end

        AZF[(Azure Files NFS v4.1<br/>Premium tier<br/>shared failover volume<br/>Cribl HA requirement)]

        subgraph WORKER_VMS["Worker VM Scale Sets"]
            W_DEV[dev-* WGs<br/>4 scale sets]
            W_TEST[test-* WGs<br/>4 scale sets]
            W_ALT[altprod-* WGs<br/>4 scale sets]
            W_PROD[prod-* WGs<br/>4 scale sets]
        end

        LB --> L1
        LB -.-> L2
        L1 ---|mount| AZF
        L2 ---|mount| AZF

        L1 -.->|port 4200<br/>10s poll| W_DEV
        L1 -.->|port 4200| W_TEST
        L1 -.->|port 4200| W_ALT
        L1 -.->|port 4200| W_PROD
    end

    HARNESS -->|HTTPS :9000<br/>/version/sync| LB
    GH -.->|leader pulls<br/>on sync command| L1

    style LEADER_PAIR fill:#2d6a4f,color:#fff
    style AZF fill:#0369a1,color:#fff
    style W_PROD fill:#7f1d1d,color:#fff
    style W_ALT fill:#78350f,color:#fff
    style W_TEST fill:#1e3a5f,color:#fff
    style W_DEV fill:#3f3f46,color:#fff
```

---

## 11. Drift Detection & Self-Healing

```mermaid
flowchart TD
    CRON([Every 30 min<br/>GitHub Actions cron])
    LOOP[For each region]
    GET_LEADER[GET /api/v1/version<br/>on each regional leader<br/>via LB VIP]
    GET_GIT[git rev-parse origin/main<br/>get expected SHA]
    COMPARE{SHA match?}
    OK[No drift<br/>log to monitoring]
    HEAL[Auto-heal:<br/>call cribl-sync.sh<br/>re-trigger /version/sync]
    ALERT[Notify Slack<br/>warning channel<br/>not paging severity]
    AUDIT[Log drift event<br/>for trend analysis]

    CRON --> LOOP --> GET_LEADER --> GET_GIT --> COMPARE
    COMPARE -->|yes| OK
    COMPARE -->|no| HEAL
    HEAL --> ALERT
    HEAL --> AUDIT

    style COMPARE fill:#78350f,color:#fff
    style HEAL fill:#991b1b,color:#fff
    style OK fill:#2d6a4f,color:#fff
```

---

## 12. Rollback Flow

```mermaid
flowchart LR
    DETECT[Canary metrics<br/>breach SLO<br/>OR<br/>incident reported]
    REVERT[gh api PATCH main HEAD~1<br/>force push revert commit]
    WEBHOOK[Push triggers<br/>normal pipeline]
    SYNC[POST /version/sync<br/>parallel to 4 regions]
    VERIFY[Verify rollback SHA<br/>across all leaders]
    NOTIFY[Page on-call<br/>+ incident ticket<br/>+ post-mortem scheduled]

    DETECT --> REVERT --> WEBHOOK --> SYNC --> VERIFY --> NOTIFY

    style DETECT fill:#991b1b,color:#fff
    style REVERT fill:#78350f,color:#fff
    style NOTIFY fill:#7f1d1d,color:#fff
```

---

## 13. Migration Phases

```mermaid
flowchart TD
    P1[Phase 1: Audit + Cleanup<br/>audit NFS-local Git<br/>scrub secrets<br/>verify Cribl Enterprise<br/>1 week]
    P2[Phase 2: Mirror to GitHub<br/>push current NFS Git<br/>set up branch protection<br/>CODEOWNERS<br/>1 week]
    P3[Phase 3: Pilot region<br/>connect Waukegan leader<br/>CRIBL_GIT_OPS=None<br/>UI still writable<br/>1 week]
    P4[Phase 4: Build automation<br/>Harness pipeline<br/>promote.sh<br/>validation scripts<br/>drift detection<br/>3 weeks]
    P5[Phase 5: Roll out connectivity<br/>remaining 3 regions to GitHub<br/>still UI-writable<br/>drift detection running<br/>2 weeks]
    P6[Phase 6: Framework refactor<br/>dual-mode framework<br/>API in dev, PR in test/altprod/prod<br/>3 weeks]
    P7[Phase 7: Cutover to GitOps Push<br/>one region at a time<br/>Waukegan first<br/>1 week soak per region<br/>UI becomes read-only<br/>4 weeks]
    P8[Phase 8: Operationalize<br/>decommission UI editing<br/>onboard engineers to PR workflow<br/>sandbox VM<br/>retrospective<br/>2 weeks]

    P1 --> P2 --> P3 --> P4 --> P5 --> P6 --> P7 --> P8

    style P1 fill:#1e3a5f,color:#fff
    style P7 fill:#7f1d1d,color:#fff
    style P8 fill:#2d6a4f,color:#fff
```

---

## 14. Repository Structure (visual tree)

```mermaid
flowchart TD
    ROOT["hcsc/cribl-config<br/>(branch: main)"]
    GH_DIR[".github/"]
    GH_WF["workflows/<br/>validate-pr.yml<br/>auto-merge-framework.yml<br/>drift-detect.yml"]
    GH_CO["CODEOWNERS<br/>per-folder reviewers"]
    HARNESS_DIR[".harness/pipelines/<br/>cribl-config-sync.yaml"]
    GROUPS["groups/"]
    DEV_GRP["dev-aro/, dev-filebeat/<br/>dev-syslog/, dev-wec/"]
    TEST_GRP["test-aro/, test-filebeat/<br/>test-syslog/, test-wec/"]
    ALT_GRP["altprod-aro/, altprod-filebeat/<br/>altprod-syslog/, altprod-wec/"]
    PROD_GRP["prod-aro/, prod-filebeat/<br/>prod-syslog/, prod-wec/"]
    LOCAL["local/cribl/<br/>leader-level config"]
    SCRIPTS["scripts/<br/>cribl-sync.sh<br/>promote.sh<br/>validate-against-cribl.sh<br/>canary-metrics-check.sh<br/>verify-wgs.sh"]
    IGNORE[".gitignore<br/>cribl.secret, ssh keys, certs"]

    ROOT --> GH_DIR
    GH_DIR --> GH_WF
    GH_DIR --> GH_CO
    ROOT --> HARNESS_DIR
    ROOT --> GROUPS
    GROUPS --> DEV_GRP
    GROUPS --> TEST_GRP
    GROUPS --> ALT_GRP
    GROUPS --> PROD_GRP
    ROOT --> LOCAL
    ROOT --> SCRIPTS
    ROOT --> IGNORE

    style ROOT fill:#1e3a5f,color:#fff
    style DEV_GRP fill:#3f3f46,color:#fff
    style TEST_GRP fill:#1e3a5f,color:#fff
    style ALT_GRP fill:#78350f,color:#fff
    style PROD_GRP fill:#7f1d1d,color:#fff
```

---

## 15. CODEOWNERS Access Control (folder to reviewer mapping)

```mermaid
flowchart LR
    DEV["groups/dev-*"]
    TEST["groups/test-*"]
    ALT["groups/altprod-*"]
    PROD["groups/prod-*"]
    LEADER["local/"]

    CRIBL_TEAM[cribl-team]
    LEADS[cribl-leads]
    SEC[security-team]
    STAFF[cribl-staff-eng]

    DEV --> CRIBL_TEAM
    TEST --> CRIBL_TEAM
    ALT --> LEADS
    PROD --> LEADS
    PROD --> SEC
    LEADER --> STAFF

    style DEV fill:#3f3f46,color:#fff
    style TEST fill:#1e3a5f,color:#fff
    style ALT fill:#78350f,color:#fff
    style PROD fill:#7f1d1d,color:#fff
    style LEADER fill:#1e2761,color:#fff
```

---

## 16. Summary — The Full Picture

```mermaid
flowchart TB
    subgraph INPUTS["Change Sources"]
        ENG[Engineer]
        FW[cribl-framework]
    end

    subgraph GITHUB["GitHub (single source of truth)"]
        REPO[(hcsc/cribl-config<br/>branch: main)]
        CI[GitHub Actions:<br/>validate + drift detect]
        CO[CODEOWNERS<br/>per-folder review]
    end

    subgraph HARNESS_BLOCK["Harness CI/CD"]
        PIPE[cribl-config-sync.yaml<br/>detect / validate / approve<br/>canary / deploy / verify]
        SCRIPT[scripts/cribl-sync.sh<br/>POST /api/v1/version/sync]
    end

    subgraph CRIBL["Cribl Stream — 4 Regions"]
        WAU[Waukegan Leader<br/>UI read-only]
        FTW[Fort Worth Leader<br/>UI read-only]
        AZN[Azure North Leader<br/>UI read-only]
        AZS[Azure South Leader<br/>UI read-only]
        WG[64 Worker Groups<br/>16 per region x 4 regions]
    end

    ENG -->|PR| REPO
    FW -->|PR via API| REPO
    REPO --> CI
    REPO --> CO
    CI -.->|all checks pass| REPO
    REPO -->|webhook on merge| PIPE
    PIPE --> SCRIPT
    SCRIPT -->|parallel sync| WAU
    SCRIPT -->|parallel sync| FTW
    SCRIPT -->|parallel sync| AZN
    SCRIPT -->|parallel sync| AZS
    WAU -.->|10s poll| WG
    FTW -.->|10s poll| WG
    AZN -.->|10s poll| WG
    AZS -.->|10s poll| WG

    style REPO fill:#1e3a5f,color:#fff
    style PIPE fill:#0b4f6c,color:#fff
    style SCRIPT fill:#1e2761,color:#fff
    style WAU fill:#2d6a4f,color:#fff
    style FTW fill:#2d6a4f,color:#fff
    style AZN fill:#2d6a4f,color:#fff
    style AZS fill:#2d6a4f,color:#fff
    style WG fill:#166534,color:#fff
```
