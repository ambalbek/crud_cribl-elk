# Observability Onboarding Service — Business View

**Audience:** platform leadership, product owners, funding approvers
**Question this answers:** what changes for the business, and how do we know it worked

---

## 1. The problem in one picture

Onboarding an application into the observability platform today is a manual, expert-dependent
process. A request arrives through a form, and a small number of platform engineers translate it
by hand into Cribl routes, Elasticsearch roles, blob containers, and Dynatrace configuration —
each in a different tool, each with its own failure mode.

```mermaid
flowchart LR
    subgraph TODAY["Today — manual, serialized, expert-bound"]
        direction TB
        A1["App team<br/>submits form"] --> A2["Email / ticket<br/>lands in a queue"]
        A2 --> A3["Engineer reads<br/>the request"]
        A3 --> A4["Engineer hand-builds<br/>Cribl route"]
        A4 --> A5["Engineer hand-builds<br/>ELK role + index"]
        A5 --> A6["Engineer requests<br/>blob storage"]
        A6 --> A7["Engineer configures<br/>Dynatrace"]
        A7 --> A8["Engineer emails<br/>the app team"]
        A8 --> A9{"Did it<br/>work?"}
        A9 -->|No| A3
    end

    style TODAY fill:#fff5f5,stroke:#c53030
```

Every arrow above is a handoff, and every handoff is a place where the request waits. The engineer
is the integration layer.

---

## 2. What we are building instead

```mermaid
flowchart LR
    subgraph TARGET["Target — governed, parallel, self-service"]
        direction TB
        B1["App team submits<br/>through SharePoint / AYS"] --> B2["Request enters<br/>governed pipeline"]
        B2 --> B3["Automated validation<br/>+ intake review"]
        B3 --> B4["Guided solutioning<br/>schema + tagging enforced"]
        B4 --> B5["Delivery executes<br/>in parallel"]
        B5 --> B6["Automated verification"]
        B6 --> B7["Turnover package<br/>issued to app team"]
        B6 -->|Failure| B8["Auto-incident<br/>in ServiceNow"]
    end

    style TARGET fill:#f0fff4,stroke:#2f855a
```

The engineer stops being the integration layer and becomes the reviewer of an automated one.

---

## 3. Capability map — what becomes possible

```mermaid
flowchart TD
    ROOT["Observability Onboarding Service"]

    ROOT --> C1["Self-service intake"]
    ROOT --> C2["Governed solutioning"]
    ROOT --> C3["Automated delivery"]
    ROOT --> C4["Lifecycle management"]
    ROOT --> C5["Audit and compliance"]

    C1 --> C1a["Single front door via SharePoint / AYS"]
    C1 --> C1b["Request status visible to the requester"]
    C1 --> C1c["No engineer needed to accept work"]

    C2 --> C2a["Standard onboarding workbook"]
    C2 --> C2b["Schema and tagging enforced at intake"]
    C2 --> C2c["Entity and field mapping captured once"]

    C3 --> C3a["Cribl routes and destinations"]
    C3 --> C3b["ELK roles, indexes, ILM tiers"]
    C3 --> C3c["Azure Blob container and retention"]
    C3 --> C3d["Dynatrace zones and agents"]

    C4 --> C4a["Live service catalog of every onboarded app"]
    C4 --> C4b["Offboarding that actually removes everything"]
    C4 --> C4c["Re-onboarding and config drift correction"]

    C5 --> C5a["Immutable audit trail per request"]
    C5 --> C5b["Who approved what, and when"]
    C5 --> C5c["Entitlement lookup across all clusters"]
```

Three of these five capabilities partially exist today in the current framework. The upgrade
completes them and adds the two that do not exist at all: governed solutioning and full
lifecycle management.

---

## 4. Value drivers

| Driver | Mechanism | Who benefits |
|---|---|---|
| **Cycle time reduction** | Parallel delivery execution replaces serialized manual steps | App teams waiting to ship |
| **Engineer capacity recovered** | Routine onboarding stops consuming senior engineer hours | Platform team; frees time for architecture work |
| **Consistency** | Every app gets the same route shape, role shape, retention tier | Operations; fewer one-off configurations to support |
| **Cost control** | Retention tier is a required, governed field — not an afterthought | Finance; storage and ingest spend become predictable |
| **Reduced rework** | Validation at intake catches bad requests before delivery starts | Both sides; fewer round-trips |
| **Audit readiness** | Immutable state transitions and entitlement records | Compliance, internal audit |
| **Offboarding hygiene** | Automated teardown of routes, roles, and containers | Security; removes orphaned access and orphaned spend |

---

## 5. Measurement — instrument these, do not assume them

The numbers below are **placeholders for a baseline you should measure before Phase 1 ships.**
Claiming a specific percentage improvement before measuring the current state is how platform
projects lose credibility at the second review.

| Metric | How to capture | Baseline | Target |
|---|---|---|---|
| Median time from request submitted → delivered | State transition timestamps | *measure* | *set after baseline* |
| 90th percentile time to deliver | Same | *measure* | *set after baseline* |
| Engineer-hours per onboarding | Time tracking or sampled estimate | *measure* | *set after baseline* |
| Requests requiring rework | Count of `INTAKE_ENGAGEMENT` revision loops | *measure* | *set after baseline* |
| Delivery task failure rate, by adapter | `DeliveryTask` status | n/a — new | < 5% |
| Apps in catalog with drifted config | Catalog reconciliation scan | unknown today | trend to zero |
| Orphaned resources after offboarding | Offboard verification step | unknown today | zero |
| Percentage of requests fully self-service | Requests with no manual intervention | ~0% | *set after baseline* |

The state machine gives you these for free once Phase 1 lands — every transition is timestamped
and attributed. That instrumentation is itself a deliverable worth funding.

---

## 6. Delivery roadmap

```mermaid
gantt
    title Phased delivery — each phase is independently valuable
    dateFormat YYYY-MM-DD
    axisFormat %b

    section Foundation
    Audit and decisions           :p0, 2026-08-01, 2w
    Postgres data layer           :p1, after p0, 4w
    State machine + instrumentation :p1b, after p0, 4w

    section Automation
    Delivery adapters             :p2, after p1, 6w
    Dynatrace service             :p2b, after p1, 4w
    Harness storage integration   :p2c, after p1, 3w

    section Experience
    Intake and solutioning UI     :p3, after p2, 5w
    Turnover packages             :p3b, after p2, 3w

    section Enterprise readiness
    ForgeRock SSO and Key Vault   :p4, after p3, 4w
    ARO deployment and CI/CD      :p5, after p4, 4w
    Documentation and runbooks    :p6, after p5, 2w
```

Durations are planning estimates, not commitments. Sequence matters more than duration — the
Postgres layer gates everything downstream because it is where measurement lives.

---

## 7. What lands when

| Phase | Business outcome available at the end of the phase |
|---|---|
| **0 — Audit** | Honest inventory of the current platform, including security debt. Decision record for leadership sign-off. |
| **1 — Data layer** | Every request measurable end to end. Reporting on cycle time becomes possible for the first time. |
| **2 — Delivery adapters** | Delivery runs in parallel and is repeatable. Failure is visible per sub-task rather than as one opaque error. |
| **3 — Intake and solutioning** | App teams self-serve. Schema and tagging discipline enforced at source, not corrected later. |
| **4 — Auth and secrets** | Enterprise SSO, no shared local accounts, no credentials on disk. Audit-defensible. |
| **5 — ARO and CI/CD** | Production-grade hosting, standard deployment path, rollback capability. |
| **6 — Documentation** | Operable by the whole team, not just the author. Removes a key-person dependency. |

Phases 0, 1, and 4 are worth doing even if the rest is deferred — measurement and security debt
retirement stand alone.

---

## 8. Risks, stated plainly

```mermaid
flowchart TD
    R1["Elasticsearch → PostgreSQL<br/>migration"] --> M1["Mitigate: dual-write phase,<br/>backfill, validate, then cut over"]
    R2["Downstream API changes<br/>Cribl, DT, Harness, ServiceNow"] --> M2["Mitigate: adapter pattern isolates<br/>each vendor behind one interface"]
    R3["ForgeRock / IIQ integration<br/>depends on other teams"] --> M3["Mitigate: sequence as Phase 4,<br/>keep local auth working until cutover"]
    R4["Key-person dependency<br/>on the current author"] --> M4["Mitigate: Phase 6 docs and runbooks<br/>are a funded deliverable, not optional"]
    R5["Adoption — teams keep<br/>emailing the platform team"] --> M5["Mitigate: close the side door<br/>once the front door works"]
    R6["Credential exposure<br/>in existing repo history"] --> M6["Mitigate: audit in Phase 0,<br/>rotate before anything else ships"]

    style R6 fill:#fff5f5,stroke:#c53030
    style R1 fill:#fffaf0,stroke:#dd6b20
```

Risk 6 is not theoretical — the Phase 0 audit exists specifically to confirm or clear it, and it
should be resolved before any other work is funded.

---

## 9. What this does not solve

Being explicit here protects the project from being blamed for problems it was never scoped to fix.

- It does not reduce **ingest volume or storage cost by itself**. It makes retention tier a
  governed decision, which enables cost work — it does not perform that work.
- It does not replace **capacity planning** for the Cribl or Elasticsearch estates.
- It does not fix **application-side logging quality**. Enforcing a schema at onboarding raises
  the floor; it does not make a noisy application quiet.
- It does not eliminate the **engineering review step** for complex or high-volume onboardings.
  It removes review from routine ones.
- It is not a **replacement for ServiceNow**. It integrates with AYS; it does not compete with it.
