# Cribl Framework — C4 Architecture Diagrams

## Level 1 — System Context

> Who uses the system and what external systems does it depend on?

```mermaid
C4Context
    title System Context — Cribl Framework Enterprise

    Person(client, "Application Team", "Requests log routing<br>onboarding via the portal")
    Person(platform, "Platform / Ops Team", "Provisions routes, destinations,<br>and ELK roles")

    System(criblfw, "Cribl Framework", "Web portal + automation tools<br>for onboarding application logs<br>into Cribl Stream and ELK")

    System_Ext(cribl, "Cribl Stream", "Log routing and processing<br>platform (worker groups)")
    System_Ext(elk_data, "Elasticsearch — Datastream", "Stores onboarding requests<br>and tracks their status")
    System_Ext(elk_ent, "Elasticsearch — Entitlements", "Stores ELK role mappings<br>for identity-based access")
    System_Ext(azure, "Azure Blob Storage", "Final destination for routed<br>application logs (per region/env)")
    System_Ext(ad, "Active Directory", "Source of entitlement groups<br>referenced in ELK role mappings")

    Rel(client, criblfw, "Submits onboarding request", "HTTPS")
    Rel(platform, criblfw, "Provisions routes, roles,<br>and reviews entitlements", "HTTPS")

    Rel(criblfw, cribl, "Creates routes and destinations", "REST API / HTTPS")
    Rel(criblfw, elk_data, "Indexes and updates<br>onboarding requests", "REST API / HTTPS")
    Rel(criblfw, elk_ent, "Reads role mappings<br>for entitlement lookup", "REST API / HTTPS")
    Rel(criblfw, elk_ent, "Creates roles and<br>role-mappings for apps", "REST API / HTTPS")
    Rel(cribl, azure, "Routes logs to", "HTTPS / Azure SDK")
    Rel(elk_ent, ad, "References entitlement group DNs", "LDAP / embedded")
```

---

## Level 2 — Container Diagram

> What deployable units make up the system?

```mermaid
C4Container
    title Container Diagram — Cribl Framework Enterprise

    Person(client, "Application Team")
    Person(platform, "Platform / Ops Team")

    System_Boundary(criblfw, "Cribl Framework") {
        Container(apache, "Apache httpd", "Reverse Proxy", "Terminates TLS, enforces<br>security headers, proxies to Flask")
        Container(flask, "cribl-framework", "Python 3.13 / Flask 3.1 · port 5000", "Portal UI, Pusher UI,<br>Entitlements, RBAC")
        Container(cribl_svc, "cribl-service", "Python 3.13 / FastAPI · port 8000", "REST API wrapping Cribl Stream:<br>routes, destinations, pipelines,<br>worker groups, leaders")
        Container(ece_svc, "ece-service", "Python 3.13 / FastAPI · port 8001", "REST API wrapping ECE/ELK:<br>roles, role-mappings, indexes,<br>Logstash pipelines, Kibana dashboards")
        Container(rode_rm, "rode_rm.py", "Python CLI (subprocess)", "Provisions ELK roles and<br>role-mappings")
        ContainerDb(snapshots, "cribl_snapshots/", "JSON files on disk", "Route table snapshots<br>for rollback")
        ContainerDb(templates_out, "ops_rm_r_templates_output/", "JSON files on disk", "Generated ELK role/<br>role-mapping files")
    }

    System_Ext(cribl, "Cribl Stream", "REST API")
    System_Ext(elk_data, "Elasticsearch — Datastream", "REST API")
    System_Ext(elk_ent, "Elasticsearch — Entitlements", "REST API")
    System_Ext(azure, "Azure Blob Storage")

    Rel(client, apache, "Submits onboarding request", "HTTPS")
    Rel(platform, apache, "Manages provisioning", "HTTPS")
    Rel(apache, flask, "Proxies requests", "HTTP")
    Rel(flask, cribl_svc, "Route/destination/pipeline<br>provisioning", "REST HTTP :8000")
    Rel(flask, ece_svc, "Role / index / pipeline<br>management", "REST HTTP :8001")
    Rel(flask, rode_rm, "Spawns subprocess", "CLI args / stdout")
    Rel(cribl_svc, cribl, "GET/PATCH routes,<br>POST destinations,<br>CRUD pipelines", "REST / HTTPS")
    Rel(cribl_svc, snapshots, "Writes route snapshots")
    Rel(rode_rm, elk_ent, "PUT roles and<br>role-mappings", "REST / HTTPS")
    Rel(rode_rm, templates_out, "Writes ELK template files")
    Rel(flask, elk_data, "Index and update<br>onboarding requests", "REST / HTTPS")
    Rel(flask, elk_ent, "Read role mappings<br>for entitlement UI", "REST / HTTPS")
    Rel(cribl, azure, "Streams logs to", "HTTPS")
```

---

## Level 3 — Component Diagram (Flask Web App)

> What are the major components inside the Flask container?

```mermaid
C4Component
    title Component Diagram — Flask Web App (app.py)

    Person(client, "Application Team")
    Person(platform, "Platform / Ops Team")

    Container_Boundary(flask, "Flask Web App") {
        Component(auth, "Auth Module", "Flask session + config.json", "Login/logout, RBAC enforcement<br>(admin vs user roles)")
        Component(portal, "Onboarding Portal", "Flask routes: /portal", "Request form UI and<br>POST /portal/api/submit")
        Component(admin_ui, "Admin Status UI", "Flask route: /portal/admin/update-status", "Marks onboarding requests<br>as done in Elasticsearch")
        Component(cribl_ui, "Cribl Pusher UI", "Flask routes: /cribl, /cribl/api/*", "Web interface to run<br>cribl-pusher.py and rode_rm.py subprocesses")
        Component(ent_ui, "Entitlement Lookup UI", "Flask routes: /entitlements, /api/entitlements", "Fetches and displays ELK<br>role-mapping data across clusters")
        Component(health, "Health Endpoints", "Flask routes: /health, /health/es", "Liveness and Elasticsearch<br>connectivity checks")
        Component(es_client, "Elasticsearch Client", "requests + config.json", "Index documents, update by query,<br>fetch role mappings")
    }

    System_Ext(elk_data, "Elasticsearch — Datastream")
    System_Ext(elk_ent, "Elasticsearch — Entitlements")
    ContainerDb(config, "config.json", "Runtime config", "Credentials, workspace<br>definitions, cluster URLs")

    Rel(client, auth, "Logs in", "HTTPS POST /login")
    Rel(client, portal, "Submits request", "HTTPS")
    Rel(platform, auth, "Logs in as admin", "HTTPS POST /login")
    Rel(platform, cribl_ui, "Runs provisioning", "HTTPS")
    Rel(platform, admin_ui, "Updates status", "HTTPS")
    Rel(platform, ent_ui, "Looks up entitlements", "HTTPS")

    Rel(auth, config, "Reads local accounts")
    Rel(portal, es_client, "Index new request")
    Rel(admin_ui, es_client, "Update request status")
    Rel(ent_ui, es_client, "Fetch role mappings")
    Rel(cribl_ui, config, "Reads workspace config")
    Rel(es_client, elk_data, "POST / _update_by_query", "REST / HTTPS")
    Rel(es_client, elk_ent, "GET _security/role_mapping", "REST / HTTPS")
```

---

## Level 3 — Component Diagram (cribl_service)

> What are the major components inside the cribl-service container?

```mermaid
C4Component
    title Component Diagram — cribl-service (FastAPI)

    Person(flask_app, "cribl-framework", "Flask portal calling cribl-service over HTTP")

    Container_Boundary(cs, "cribl-service") {
        Component(main, "main.py", "FastAPI app factory", "Registers all routers,<br>configures logging, /health")
        Component(settings, "settings.py", "Config module", "Reads CRIBL_BASE_URL,<br>CRIBL_TOKEN, CRIBL_USERNAME,<br>CRIBL_PASSWORD, CRIBL_SKIP_SSL<br>from environment variables")
        Component(deps, "CriblClient (deps.py)", "HTTP client + DI dependency", "Authenticates with Cribl,<br>re-implements die()-calling functions<br>as HTTPException-raising methods,<br>reuses pure-logic from cribl_api.py")

        Component(r_routes, "routes router", "FastAPI APIRouter", "GET/PATCH table,<br>POST route (smart insert),<br>DELETE route by name/id")
        Component(r_dest, "destinations router", "FastAPI APIRouter", "Full CRUD for<br>Cribl outputs (/system/outputs)")
        Component(r_pipe, "pipelines router", "FastAPI APIRouter", "Full CRUD for<br>Cribl pipelines")
        Component(r_wg, "worker_groups router", "FastAPI APIRouter", "GET /master/groups<br>(leader-level)")
        Component(r_lead, "leaders router", "FastAPI APIRouter", "GET system/info<br>and git-settings")
    }

    ContainerDb(shared, "Shared root modules", "cribl_api.py / cribl_utils.py", "Pure-logic functions:<br>normalize_route, find_default_route_index,<br>count_all_routes, unwrap_response")

    System_Ext(cribl, "Cribl Stream", "REST API")

    Rel(flask_app, main, "HTTP requests", "REST / HTTP :8000")
    Rel(main, r_routes, "Includes router")
    Rel(main, r_dest, "Includes router")
    Rel(main, r_pipe, "Includes router")
    Rel(main, r_wg, "Includes router")
    Rel(main, r_lead, "Includes router")
    Rel(r_routes, deps, "Depends(get_cribl_client)")
    Rel(r_dest, deps, "Depends(get_cribl_client)")
    Rel(r_pipe, deps, "Depends(get_cribl_client)")
    Rel(r_wg, deps, "Depends(get_cribl_client)")
    Rel(r_lead, deps, "Depends(get_cribl_client)")
    Rel(deps, settings, "Reads env vars")
    Rel(deps, shared, "Imports normalize_route,<br>find_default_route_index,<br>unwrap_response")
    Rel(deps, cribl, "Authenticated HTTP calls", "REST / HTTPS")
```

---

## Level 3 — Component Diagram (ece_service)

> What are the major components inside the ece-service container?

```mermaid
C4Component
    title Component Diagram — ece-service (FastAPI)

    Person(flask_app, "cribl-framework", "Flask portal calling ece-service over HTTP")

    Container_Boundary(es, "ece-service") {
        Component(main2, "main.py", "FastAPI app factory", "Registers all routers,<br>configures logging, /health")
        Component(settings2, "settings.py", "Config module", "Reads ECE_ES_URL, ECE_ES_TOKEN,<br>ECE_KIBANA_URL, etc.<br>from environment variables")
        Component(deps2, "ECEClient (deps.py)", "HTTP client + DI dependency", "Authenticated sessions for<br>ES nonprod, ES prod, Kibana.<br>Wraps role_rm.py safe functions.<br>Raises HTTPException — never die()")

        Component(r_roles, "roles router", "FastAPI APIRouter", "CRUD + /generate (templates only)<br>+ /provision (push to nonprod+prod)")
        Component(r_rm, "role_mappings router", "FastAPI APIRouter", "CRUD for ES<br>/_security/role_mapping")
        Component(r_idx, "indexes router", "FastAPI APIRouter", "CRUD for ES indexes<br>+ index templates")
        Component(r_ls, "logstash_pipelines router", "FastAPI APIRouter", "CRUD for<br>/_logstash/pipeline")
        Component(r_kib, "kibana_dashboards router", "FastAPI APIRouter", "CRUD via Kibana<br>saved objects API")
    }

    ContainerDb(role_rm_mod, "role_rm.py (root)", "Python module", "generate_templates,<br>_parse_kibana_console,<br>push_elk, save_templates<br>(all safe — no die())")
    ContainerDb(tmpl_out, "ops_rm_r_templates_output/", "JSON files on disk", "Generated role/role-mapping<br>files per app")

    System_Ext(elk_np, "Elasticsearch — Nonprod", "REST API")
    System_Ext(elk_p, "Elasticsearch — Prod", "REST API")
    System_Ext(kibana, "Kibana", "Saved Objects API")

    Rel(flask_app, main2, "HTTP requests", "REST / HTTP :8001")
    Rel(main2, r_roles, "Includes router")
    Rel(main2, r_rm, "Includes router")
    Rel(main2, r_idx, "Includes router")
    Rel(main2, r_ls, "Includes router")
    Rel(main2, r_kib, "Includes router")
    Rel(r_roles, deps2, "Depends(get_ece_client)")
    Rel(r_rm, deps2, "Depends(get_ece_client)")
    Rel(r_idx, deps2, "Depends(get_ece_client)")
    Rel(r_ls, deps2, "Depends(get_ece_client)")
    Rel(r_kib, deps2, "Depends(get_ece_client)")
    Rel(deps2, settings2, "Reads env vars")
    Rel(deps2, role_rm_mod, "Imports generate_templates,<br>push_elk, save_templates")
    Rel(deps2, tmpl_out, "Writes generated templates")
    Rel(deps2, elk_np, "ES API calls (nonprod)", "REST / HTTPS")
    Rel(deps2, elk_p, "ES API calls (prod)", "REST / HTTPS")
    Rel(deps2, kibana, "Kibana API calls", "REST / HTTPS")
```

---

## Diagram Summary

| Level | Diagram | Audience |
|-------|---------|----------|
| 1 — System Context | Who uses it, what it integrates with | Everyone — management, clients, ops |
| 2 — Container | Deployable units and their communication | Architects, DevOps |
| 3 — Component (Flask) | Internal structure of cribl-framework | Developers |
| 3 — Component (cribl-service) | Internal structure of cribl-service | Developers |
| 3 — Component (ece-service) | Internal structure of ece-service | Developers |
