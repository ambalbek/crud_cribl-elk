# OTLP Observability Pipeline

> **Apps** &rarr; **Cribl Edge** &rarr; **Cribl Stream** &rarr; **Elasticsearch** &rarr; **Kibana**
>
> All telemetry flows over a single OTLP port. Cribl Stream auto-classifies signals via `_dataType` — no manual tagging required.

---

## End-to-End Data Flow

```mermaid
flowchart TD
    subgraph apps["&nbsp; Application Layer &nbsp;"]
        A1["cribl-framework\nFlask :5000\nOTel SDK"]
        A2["cribl_service\nFastAPI :8001\nOTel SDK"]
        A3["ece_service\nFastAPI :8002\nOTel SDK"]
    end

    subgraph edge["&nbsp; Cribl Edge &nbsp;·&nbsp; :4317 gRPC &nbsp;·&nbsp; :4318 HTTP &nbsp;·&nbsp; UI :9420 &nbsp;"]
        SRC["Source\nOTLP In (otlp_in)"]
        PIPE["Pipelines\nClassify · Enrich · Filter · Transform"]
        DEST["Destination\nOTLP Out → Cribl Stream"]
    end

    subgraph stream["&nbsp; Cribl Stream &nbsp;·&nbsp; :4317 OTLP &nbsp;"]
        OTLP_IN["Source\nOTLP In :4317\nauto-sets _dataType"]
        RT["Routes by _dataType\ntraces → pipeline_traces\nmetrics → pipeline_metrics\nlogs → pipeline_logs"]
        OUT_ES["Destination\nElasticsearch :9200\ndata_stream: true"]
    end

    subgraph es["&nbsp; Elasticsearch :9200 &nbsp;"]
        DS_T["traces-otel-default"]
        DS_M["metrics-otel-default"]
        DS_L["logs-otel-default"]
        ILM["ILM Policy\nHot → Warm → Cold → Delete"]
    end

    subgraph kibana["&nbsp; Kibana :5601 &nbsp;"]
        K1["Discover"]
        K2["Dashboards"]
        K3["APM / Trace Explorer"]
        K4["Observability Overview"]
    end

    A1 -->|"OTLP/HTTP"| SRC
    A2 -->|"OTLP/HTTP"| SRC
    A3 -->|"OTLP/HTTP"| SRC

    SRC --> PIPE --> DEST

    DEST -->|"OTLP :4317 · all signals"| OTLP_IN

    OTLP_IN --> RT --> OUT_ES

    OUT_ES --> DS_T
    OUT_ES --> DS_M
    OUT_ES --> DS_L

    DS_T --> ILM
    DS_M --> ILM
    DS_L --> ILM

    DS_T --> K1 & K3 & K4
    DS_M --> K1 & K2 & K4
    DS_L --> K1 & K4
```

---

## How Signal-Type Tagging Works

OTLP multiplexes all three signal types over a **single port**. The protocol itself separates them — no extra ports, no manual tags.

| Signal | OTLP HTTP Path | OTLP gRPC Service | Cribl `_dataType` |
|:------:|:--------------:|:------------------:|:-----------------:|
| Traces | `/v1/traces` | `TraceService/Export` | `traces` |
| Metrics | `/v1/metrics` | `MetricsService/Export` | `metrics` |
| Logs | `/v1/logs` | `LogService/Export` | `logs` |

Cribl Stream's OTLP source **automatically** populates `_dataType` based on the incoming path/service. Routes then fan out to signal-specific pipelines:

```
Route 1:   _dataType == 'traces'   →  pipeline_traces   →  Elasticsearch
Route 2:   _dataType == 'metrics'  →  pipeline_metrics   →  Elasticsearch
Route 3:   _dataType == 'logs'     →  pipeline_logs      →  Elasticsearch
```

---

## Step-by-Step Breakdown

### Step 1 &mdash; Application Layer: Emit Telemetry

```mermaid
flowchart LR
    APP["App Service\n(Flask / FastAPI)"]
    SDK["OTel SDK\notel_setup.py"]
    AUTO["Auto-Instrumentation\nHTTP · Routes · DB"]
    EDGE["Cribl Edge\n:4318"]

    APP --> SDK --> AUTO -->|"OTLP/HTTP"| EDGE
```

| What happens | Detail |
|:-------------|:-------|
| Instrumentation | Each service calls `otel_setup.py` at startup |
| Auto-capture | Inbound/outbound HTTP, route handlers, DB calls |
| Export format | OTLP protobuf over HTTP to `:4318` |

---

### Step 2 &mdash; Cribl Edge: Receive, Process, Forward

```mermaid
flowchart TD
    subgraph receive["Receive"]
        GRPC[":4317 gRPC"]
        HTTP[":4318 HTTP"]
    end

    subgraph process["Process"]
        CLASSIFY["Classify\ntraces · metrics · logs"]
        ENRICH["Enrich\nmetadata, tags"]
        FILTER["Filter\ndrop noise, sample"]
        TRANSFORM["Transform\nredact PII, reshape"]
    end

    subgraph forward["Forward"]
        OUT["OTLP Out\n→ Cribl Stream :4317\nsingle connection · all signals"]
    end

    GRPC --> CLASSIFY
    HTTP --> CLASSIFY
    CLASSIFY --> ENRICH --> FILTER --> TRANSFORM --> OUT
```

| What happens | Detail |
|:-------------|:-------|
| Receive | OTLP source (`otlp_in`) on gRPC + HTTP |
| Process | Classify by signal, enrich, filter noise, transform/redact |
| Forward | Single OTLP connection carries all signals to Stream |

---

### Step 3 &mdash; Cribl Stream: Route by `_dataType` and Deliver

```mermaid
flowchart TD
    subgraph source["Source"]
        OTLP["OTLP In :4317\nauto-sets _dataType"]
    end

    subgraph routes["Routes by _dataType"]
        R_T["traces → pipeline_traces"]
        R_M["metrics → pipeline_metrics"]
        R_L["logs → pipeline_logs"]
    end

    subgraph dest["Destination"]
        ES["Elasticsearch :9200\ndata_stream: true\ndataset: otel"]
    end

    OTLP --> R_T & R_M & R_L
    R_T --> ES
    R_M --> ES
    R_L --> ES
```

| What happens | Detail |
|:-------------|:-------|
| Receive | Single OTLP source, all signals on one port |
| Classify | `_dataType` auto-populated — no manual tagging |
| Route | Fan out by `_dataType` to signal-specific pipelines |
| Deliver | Each pipeline writes to Elasticsearch as data streams |

---

### Step 4 &mdash; Elasticsearch: Store and Manage

```mermaid
flowchart LR
    subgraph streams["Data Streams"]
        T["traces-otel-default"]
        M["metrics-otel-default"]
        L["logs-otel-default"]
    end

    subgraph ilm["Index Lifecycle Management"]
        HOT["Hot\n(active writes)"]
        WARM["Warm\n(read-only)"]
        COLD["Cold\n(compressed)"]
        DEL["Delete\n(purged)"]
    end

    T & M & L --> HOT
    HOT -->|"rollover"| WARM -->|"age-based"| COLD -->|"retention"| DEL
```

| What happens | Detail |
|:-------------|:-------|
| Ingest | Data streams auto-create backing indices |
| Lifecycle | ILM handles rollover, compression, retention |
| Availability | Documents are searchable immediately on write |

---

### Step 5 &mdash; Kibana: Visualize and Explore

```mermaid
flowchart LR
    ES["Elasticsearch"]
    D["Discover\nraw search"]
    DASH["Dashboards\ncustom visuals"]
    APM["APM\ntrace waterfall"]
    OBS["Observability\nhealth overview"]

    ES --> D & DASH & APM & OBS
```

| Feature | Purpose |
|:--------|:--------|
| **Discover** | Search and filter raw documents across all signals |
| **Dashboards** | Latency, error rate, throughput charts |
| **APM / Trace Explorer** | Distributed trace waterfall views |
| **Observability** | Unified health overview across all signals |

---

## Quick Reference

### Ports

| Service | Port | Protocol | Purpose |
|:--------|:----:|:--------:|:--------|
| Cribl Edge | `4317` | gRPC | OTLP gRPC receiver |
| Cribl Edge | `4318` | HTTP | OTLP HTTP receiver |
| Cribl Edge | `9420` | HTTP | Management UI |
| Cribl Stream | `4317` | gRPC / HTTP | OTLP receiver (all signals, single port) |
| Elasticsearch | `9200` | HTTP | Search and storage API |
| Kibana | `5601` | HTTP | Visualization UI |

### Configuration Files

| File | Purpose |
|:-----|:--------|
| `cribl-edge/local/cribl/sources.yml` | OTLP source config |
| `cribl-edge/local/cribl/destinations.yml` | OTLP output to Cribl Stream |
| `cribl-edge/local/cribl/routes.yml` | Edge data routing rules |
| `cribl-stream/sources.yml` | OTLP source (single port, auto `_dataType`) |
| `cribl-stream/routes.yml` | Routes by `_dataType` to pipelines |
| `cribl-stream/pipelines/` | Signal-specific pipeline configs |
| `cribl-stream/destinations/` | Elasticsearch destination config |
| `elasticsearch.yml` | ES node settings |
