# Cribl Framework — Visio Flowchart Reference

Use this document to recreate the application flowcharts in Microsoft Visio.
Each section describes shapes, connections, and swim lanes.

---

## 1. Authentication Flow (Local Accounts)

### Swim Lanes
| Lane | Actor |
|------|-------|
| 1 | User / Browser |
| 2 | Cribl Framework (Flask) |

### Shapes

| # | Shape | Text | Lane | Color |
|---|-------|------|------|-------|
| A1 | Rounded Rectangle (Start) | User visits any page | 1 | Green |
| A2 | Diamond (Decision) | Session valid? | 2 | Yellow |
| A3 | Rectangle (Process) | Redirect to /login | 2 | Blue |
| A4 | Rectangle (Process) | Show login page (username + password form) | 2 | Blue |
| A5 | Rectangle (Process) | User enters username + password | 1 | Blue |
| A6 | Rectangle (Process) | POST /login — check local_admins + local_users | 2 | Blue |
| A7 | Diamond (Decision) | Credentials match? | 2 | Yellow |
| A8 | Rectangle (Process) | Assign role (admin or user) from account type | 2 | Blue |
| A9 | Rectangle (Process) | Create Flask session (username, role, display_name) | 2 | Blue |
| A10 | Diamond (Decision) | Role = user? | 2 | Yellow |
| A11 | Rectangle (Process) | Redirect to /portal | 2 | Blue |
| A12 | Rectangle (Process) | Redirect to requested page | 2 | Blue |
| A13 | Rounded Rectangle (End) | User is authenticated | 1 | Green |
| A14 | Rectangle (Process) | Show error: "Invalid credentials" | 2 | Red |
| A15 | Rectangle (Process) | Allow access — page renders | 2 | Green |

### Connections

| From | To | Label |
|------|----|-------|
| A1 | A2 | |
| A2 | A15 | Yes (session exists) |
| A2 | A3 | No |
| A3 | A4 | |
| A4 | A5 | |
| A5 | A6 | POST |
| A6 | A7 | |
| A7 | A8 | Yes |
| A7 | A14 | No |
| A8 | A9 | |
| A9 | A10 | |
| A10 | A11 | Yes |
| A10 | A12 | No (admin) |
| A11 | A13 | |
| A12 | A13 | |
| A14 | A4 | Back to login |

---

## 2. End-to-End Onboarding Workflow

### Swim Lanes
| Lane | Actor |
|------|-------|
| 1 | Client (User role) |
| 2 | Cribl Framework |
| 3 | Elasticsearch |
| 4 | Platform Team (Admin role) |
| 5 | Cribl Stream |

### Shapes

| # | Shape | Text | Lane | Color |
|---|-------|------|------|-------|
| B1 | Rounded Rectangle (Start) | Client logs in | 1 | Green |
| B2 | Rectangle (Process) | Open /portal — onboarding form | 1 | Blue |
| B3 | Rectangle (Process) | Username + name auto-populated from session | 2 | Blue |
| B4 | Rectangle (Process) | Fill: APM ID, App Name, Region, Log Dest, Log Type, Entitlement Groups | 1 | Blue |
| B5 | Rectangle (Process) | POST /portal/api/submit | 2 | Blue |
| B6 | Rectangle (Process) | Validate + build ES document | 2 | Blue |
| B7 | Rectangle (Data) | Index document: status = "pending" | 3 | Orange |
| B8 | Rectangle (Process) | Return REQ-YYYYMMDD-XXXXXXXX | 2 | Blue |
| B9 | Rounded Rectangle (End) | Client sees Request ID | 1 | Green |
| B10 | Rounded Rectangle (Start) | Admin logs in | 4 | Green |
| B11 | Rectangle (Process) | Open /cribl/app — Pusher UI | 4 | Blue |
| B12 | Rectangle (Process) | Paste Request ID, select workspace + worker groups | 4 | Blue |
| B13 | Diamond (Decision) | Dry Run? | 2 | Yellow |
| B14 | Rectangle (Process) | Preview diff — no writes | 2 | Gray |
| B15 | Rectangle (Process) | Create destinations in Cribl | 5 | Purple |
| B16 | Rectangle (Process) | PATCH routes in Cribl | 5 | Purple |
| B17 | Rectangle (Process) | Create ELK roles + role-mappings | 3 | Orange |
| B18 | Rectangle (Process) | Auto-update status = "done" | 3 | Orange |
| B19 | Rounded Rectangle (End) | Request completed | 4 | Green |
| B20 | Rectangle (Process) | Open /entitlements — verify role mappings | 1 or 4 | Blue |

### Connections

| From | To | Label |
|------|----|-------|
| B1 | B2 | |
| B2 | B3 | |
| B3 | B4 | |
| B4 | B5 | Submit |
| B5 | B6 | |
| B6 | B7 | |
| B7 | B8 | |
| B8 | B9 | |
| B10 | B11 | |
| B11 | B12 | |
| B12 | B13 | Run |
| B13 | B14 | Yes |
| B13 | B15 | No |
| B14 | B12 | Review & retry |
| B15 | B16 | |
| B16 | B17 | If role_rm mode |
| B17 | B18 | |
| B16 | B18 | If pusher mode |
| B18 | B19 | |
| B19 | B20 | Optional |

---

## 3. RBAC Access Matrix

### Visio Table Shape

| Page / Feature | Route | User Role | Admin Role | Auth Required |
|----------------|-------|-----------|------------|---------------|
| Login Page | /login | Public | Public | No |
| Logout | /logout | Public | Public | No |
| Health Check | /health | Public | Public | No |
| ES Health | /health/es | Public | Public | No |
| Landing Page | / | Redirect to /portal | Full dashboard | Yes |
| Onboarding Portal | /portal | Allowed | Allowed | Yes |
| Submit Request API | /portal/api/submit | Allowed | Allowed | Yes |
| Entitlement Lookup | /entitlements | Allowed | Allowed | Yes |
| Entitlements API | /api/entitlements | Allowed | Allowed | Yes |
| Cribl Pusher UI | /cribl/app | Blocked (403) | Allowed | Yes (Admin) |
| Run Pusher API | /cribl/api/run-pusher | Blocked (403) | Allowed | Yes (Admin) |
| Run ROLE-RM API | /cribl/api/run-role-rm | Blocked (403) | Allowed | Yes (Admin) |
| Admin Panel | /portal/admin/update-status | Blocked (403) | Allowed | Yes (Admin) |

---

## 4. Application Architecture

### Visio Block Diagram

```
+--------------------------------------------------+
|                    Browser                        |
+--------------------------------------------------+
                       |
                       v
            +----------------------+
            | Cribl Framework      |
            | Flask :5000          |
            |                      |
            | Routes:              |
            | /login               |
            | /portal              |
            | /cribl/app           |
            | /entitlements        |
            | /portal/admin        |
            +----------+-----------+
                       |
       +---------------+---------------+
       |               |               |
       v               v               v
+------------------+  +------------------+  +------------------+
| Elasticsearch    |  | Cribl Stream     |  | config.json      |
| Clusters         |  | API              |  |                  |
|                  |  |                  |  | - Local accounts |
| - Onboarding     |  | - Routes         |  | - Workspaces     |
|   requests       |  | - Destinations   |  | - ES clusters    |
| - Role mappings  |  | - Worker groups  |  | - Credentials    |
|   (entitlements) |  |                  |  |                  |
+------------------+  +------------------+  +------------------+
```

### Visio Shape Definitions for Architecture

| # | Shape | Text | Color | Notes |
|---|-------|------|-------|-------|
| C1 | Rectangle | Browser | Light Gray | Top center |
| C2 | Rectangle | Cribl Framework (Flask :5000) | Blue | Center |
| C3 | Rectangle | Elasticsearch Clusters | Orange | Bottom left |
| C4 | Rectangle | Cribl Stream API | Red | Bottom center |
| C5 | Rectangle | config.json | Gray | Bottom right |

### Connections for Architecture

| From | To | Label | Line Style |
|------|----|-------|------------|
| C1 | C2 | HTTPS | Solid |
| C2 | C3 | Onboarding docs + Role mappings | Solid |
| C2 | C4 | Routes + Destinations | Solid |
| C2 | C5 | Read config | Dotted |

---

## 5. Entitlement Lookup Flow

### Shapes

| # | Shape | Text | Lane | Color |
|---|-------|------|------|-------|
| D1 | Rounded Rectangle (Start) | User opens /entitlements | User | Green |
| D2 | Rectangle (Process) | GET /api/entitlements | Framework | Blue |
| D3 | Rectangle (Process) | Load entitlement config from config.json | Framework | Blue |
| D4 | Subprocess | For each ES cluster | Framework | Blue |
| D5 | Rectangle (Process) | GET /_security/role_mapping (HTTPBasicAuth) | ES Cluster | Orange |
| D6 | Rectangle (Process) | Extract entitlement CNs matching filter | Framework | Blue |
| D7 | Rectangle (Process) | Parse CN from DN, build result objects | Framework | Blue |
| D8 | Diamond (Decision) | Cluster error? | Framework | Yellow |
| D9 | Rectangle (Process) | Add error record (error: true) | Framework | Red |
| D10 | Rectangle (Process) | Sort results by cluster + entitlement | Framework | Blue |
| D11 | Rectangle (Process) | Return JSON array | Framework | Blue |
| D12 | Rectangle (Process) | Render table with search, filter, sort, pagination | Browser | Green |
| D13 | Rounded Rectangle (End) | User views/exports entitlements | User | Green |

### Connections

| From | To | Label |
|------|----|-------|
| D1 | D2 | |
| D2 | D3 | |
| D3 | D4 | |
| D4 | D5 | Per cluster |
| D5 | D6 | Response |
| D6 | D7 | |
| D7 | D8 | |
| D8 | D9 | Yes |
| D8 | D4 | No (next cluster) |
| D9 | D4 | Next cluster |
| D4 | D10 | All clusters done |
| D10 | D11 | |
| D11 | D12 | JSON |
| D12 | D13 | |

---

## Visio Color Legend

| Color | Hex | Usage |
|-------|-----|-------|
| Green | #22c55e | Start/End terminators, success states |
| Blue | #0284c7 | Framework processes |
| Yellow | #f59e0b | Decision diamonds |
| Orange | #f97316 | Elasticsearch operations |
| Purple | #8b5cf6 | Cribl Stream operations |
| Red | #e53e3e | Error states, blocked access |
| Gray | #94a3b8 | Optional paths, config |

## Visio Shape Legend

| Shape | Usage |
|-------|-------|
| Rounded Rectangle | Start / End |
| Rectangle | Process step |
| Diamond | Decision / branch |
| Parallelogram | Data / document |
| Subprocess (double-sided rectangle) | Loop / iteration |
