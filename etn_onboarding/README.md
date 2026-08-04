# ETN Onboarding Service

Self-service onboarding platform for the observability stack (Cribl Edge, Blob storage). Replaces the current ETN onboarding portal with an API-driven workflow backed by a state machine that guides each request through five stages: Intake, Engagement, Solutioning, Delivery, and Validation.

## Tech Stack

- **Flask** -- web framework and orchestration layer
- **PostgreSQL** -- persistent storage for onboarding requests, audit logs, and delivery jobs
- **SQLAlchemy** -- ORM with Flask-SQLAlchemy integration
- **Alembic** (via Flask-Migrate) -- database schema migrations
- **OpenTelemetry** -- distributed tracing and structured logging

## Architecture

Every onboarding request is modelled as a finite state machine with the following stages:

```
intake_pending -> intake_validated -> engagement -> solutioning
  -> delivery_collection -> delivery_routing -> delivery_storage -> delivery_complete
  -> validation -> complete
Any state -> cancelled
```

State transitions are validated by the `state_machine` service and every transition is recorded in an immutable audit log.

During the **Delivery** stage, `DeliveryJob` records are created for each provisioning action (Cribl Edge configuration, ETN Portal updates, Harness Blob storage). Service clients for Cribl and ECE are included as stubs, ready to be wired to live APIs.

See [ARCHITECTURE.md](ARCHITECTURE.md) for a detailed breakdown.

## Local Development

### Start with Docker Compose

```bash
docker compose up --build
```

### Or run standalone

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://etn_user:etn_pass@localhost:5432/etn_onboarding
flask db upgrade
python -m flask run
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe -- returns 200 if the process is running |
| `GET` | `/ready` | Readiness probe -- verifies the database connection |
| `POST` | `/api/intake` | Submit a new onboarding request |
| `PUT` | `/api/intake/<id>/validate` | Validate intake data and advance to `intake_validated` |
| `POST` | `/api/engagement/<id>` | Record engagement details and advance to `engagement` |
| `POST` | `/api/solutioning/<id>` | Record solutioning decisions and advance to `solutioning` |
| `POST` | `/api/delivery/<id>/start` | Begin delivery -- creates delivery jobs and advances through delivery sub-stages |
| `GET` | `/api/delivery/<id>/status` | Check delivery job statuses |
| `POST` | `/api/validation/<id>` | Run validation checks and advance to `validation` / `complete` |
| `GET` | `/api/requests` | List all onboarding requests (supports filtering) |
| `GET` | `/api/requests/<id>` | Get a single request with audit log and delivery jobs |
| `POST` | `/api/requests/<id>/cancel` | Cancel a request from any non-terminal state |

## ARO / OpenShift Deployment

```bash
helm install etn-onboarding helm/etn-onboarding/ -n etn-onboarding --create-namespace
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | -- | PostgreSQL connection string |
| `FLASK_ENV` | No | `production` | Flask environment (`development` / `production`) |
| `SECRET_KEY` | No | auto-generated | Flask session secret key |
| `CRIBL_BASE_URL` | No | -- | Base URL of the Cribl Stream instance |
| `CRIBL_TOKEN` | No | -- | Bearer token for Cribl API authentication |
| `ES_URL` | No | -- | Elasticsearch cluster URL |
| `ES_AUTH_HEADER` | No | -- | Authorization header value for Elasticsearch |
| `ETN_PORTAL_URL` | No | -- | Base URL of the legacy ETN Portal |
| `HARNESS_API_URL` | No | -- | Harness API endpoint for Blob storage provisioning |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | -- | OpenTelemetry collector endpoint |
| `LOG_LEVEL` | No | `INFO` | Application log level |

## Integration Seams

The service is designed with pluggable integration clients. Each client has a working stub that logs calls and returns canned responses, making end-to-end testing possible without external dependencies.

| Integration | Client | Status | Purpose |
|-------------|--------|--------|---------|
| Cribl Stream | `CriblClient` | Stub | Configure Edge agents, create routes and destinations |
| ECE (Elastic) | `ECEClient` | Stub | Create security roles, role mappings, and indexes |
| ETN Portal | `ETNPortalClient` | Planned | Sync onboarding status back to the legacy portal |
| Harness | `HarnessClient` | Planned | Trigger Azure Blob container creation pipelines |

## Project Structure

```
etn_onboarding/
  app/
    extensions.py          # SQLAlchemy + Flask-Migrate instances
    models/
      onboarding_request.py  # OnboardingRequest model + RequestStatus enum
      audit_log.py           # AuditLog model
      delivery_job.py        # DeliveryJob model
    services/
      state_machine.py     # Transition validation + audit logging
      cribl_client.py      # Cribl Stream REST client (stub)
      ece_client.py        # ECE / Elasticsearch REST client (stub)
    routes/
      health.py            # /health and /ready probes
      intake.py            # Intake stage endpoints
      engagement.py        # Engagement stage endpoints
      solutioning.py       # Solutioning stage endpoints
      delivery.py          # Delivery stage endpoints
      validation.py        # Validation stage endpoints
      requests.py          # General request CRUD
    utils/                 # Shared utilities
  helm/
    etn-onboarding/        # Helm chart for ARO / OpenShift
  migrations/
    versions/              # Alembic migration scripts
  templates/               # Jinja2 HTML templates
```
