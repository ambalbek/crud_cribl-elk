# Azure Container Creation — GitOps Integration Plan

**Status**: Draft / Pending Review
**Created**: 2026-06-29
**Scope**: Add Azure Blob container provisioning to the onboarding pipeline via GitOps (git commit → Harness → Azure CLI) with email-based approval and webhook callback.

---

## 1. Overview

When a team submits an onboarding request through the portal, the framework should:

1. Record the request (existing)
2. Send an approval email to ops approvers (new)
3. On approval, append the new apmid to `azure_blob_account_containers.json` (new)
4. Git commit + push the change to trigger a Harness pipeline via GitHub webhook (new)
5. Harness creates the Azure container and calls back to confirm (new)
6. Portal marks the request as `done` (existing)

---

## 2. End-to-End Flow

```
User submits onboarding request
        │
        ▼
portal_submit() — POST /cribl/portal/api/submit
   ├── Validate input (existing)
   ├── es_index(doc, status="awaiting_approval")
   └── send_approval_email(request_id, apmid, approvers)
        │
        ▼
Approver clicks link in email
        │
        ▼
approve_container() — POST /cribl/portal/api/approve/<request_id>
   ├── Verify admin session
   ├── append_container_registry(apmid, region, env)
   ├── git_commit_container(apmid)  →  push to main
   └── portal_update_status(request_id, "provisioning")
        │
        ▼
GitHub webhook fires on push to main
        │
        ▼
Harness pipeline triggered
   ├── Read azure_blob_account_containers.json
   ├── az storage container create --account-name <acct> --name <apmid>
   └── POST /cribl/api/webhooks/container-created  (callback)
        │
        ▼
container_created_webhook()
   ├── portal_update_status(request_id, "done")
   └── Bust catalog cache
```

---

## 3. New Status Values

Current statuses: `pending`, `done`, `rejected`, `offboarded`

New statuses to add:

| Status | Meaning |
|--------|---------|
| `awaiting_approval` | Request submitted, email sent, waiting for admin action |
| `provisioning` | Approved, git pushed, Harness pipeline running |

The existing `done` and `rejected` statuses remain unchanged.

---

## 4. New Files

### 4.1 `azure_blob_account_containers.json`

Container registry file tracked in git. Harness reads this to know which containers to create.

```json
{
  "storage_accounts": {
    "prodstorageazn": {
      "region": "northcentralus",
      "env": "prod",
      "containers": ["apmid-001", "apmid-002"]
    },
    "prodstorageazs": {
      "region": "southcentralus",
      "env": "prod",
      "containers": ["apmid-001"]
    },
    "teststorageazn": {
      "region": "northcentralus",
      "env": "test",
      "containers": []
    },
    "teststorageazs": {
      "region": "southcentralus",
      "env": "test",
      "containers": []
    },
    "devstorageazn": {
      "region": "northcentralus",
      "env": "dev",
      "containers": []
    },
    "devstorageazs": {
      "region": "southcentralus",
      "env": "dev",
      "containers": []
    }
  }
}
```

**Naming convention**: `{env}storage{region_short}` — matches existing blob dest templates.

---

## 5. Changes to `app.py`

### 5.1 New Helper Functions (~100 lines)

#### `append_container_registry(apmid, region, env, config)`

- Reads `azure_blob_account_containers.json`
- Resolves storage account key: `{env}storage{region}` (e.g. `prodstorageazn`)
- Appends `apmid` if not already present (idempotent)
- Writes file back
- Returns `{"added": True/False, "account": account_key}`

```python
def append_container_registry(apmid: str, region: str, env: str, config: dict) -> dict:
    registry_path = SCRIPT_DIR / "azure_blob_account_containers.json"
    with open(registry_path, encoding="utf-8") as f:
        registry = json.load(f)

    account_key = f"{env}storage{region}"
    account = registry.get("storage_accounts", {}).get(account_key)
    if not account:
        raise ValueError(f"Unknown storage account: {account_key}")

    containers = account.setdefault("containers", [])
    if apmid in containers:
        log.info("container_registry — %s already in %s", apmid, account_key)
        return {"added": False, "account": account_key}

    containers.append(apmid)
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)

    log.info("container_registry — appended %s to %s", apmid, account_key)
    return {"added": True, "account": account_key}
```

#### `git_commit_container(apmid, config)`

- Uses existing `run_subprocess()` for git commands
- Commits `azure_blob_account_containers.json`
- Pushes to configured branch (push triggers GitHub webhook → Harness)

```python
def git_commit_container(apmid: str, config: dict) -> dict:
    git_cfg = config.get("gitops", {})
    branch = git_cfg.get("branch", "main")
    registry_file = "azure_blob_account_containers.json"

    out1, rc1 = run_subprocess(["git", "add", registry_file])
    if rc1 != 0:
        return {"ok": False, "step": "git add", "output": out1}

    msg = f"onboard: add container for {apmid}"
    out2, rc2 = run_subprocess(["git", "commit", "-m", msg])
    if rc2 != 0:
        return {"ok": False, "step": "git commit", "output": out2}

    out3, rc3 = run_subprocess(["git", "push", "origin", branch])
    if rc3 != 0:
        return {"ok": False, "step": "git push", "output": out3}

    log.info("git_commit_container — pushed %s for %s", registry_file, apmid)
    return {"ok": True, "branch": branch}
```

#### `send_approval_email(request_id, apmid, appname, config)`

- Reads SMTP settings from `config.notifications.email`
- Sends email with approve/reject links to configured approvers
- Links point to portal approve endpoint

```python
import smtplib
from email.message import EmailMessage

def send_approval_email(request_id: str, apmid: str, appname: str, config: dict) -> dict:
    email_cfg = config.get("notifications", {}).get("email", {})
    smtp_host = email_cfg.get("smtp_host", "")
    smtp_port = email_cfg.get("smtp_port", 25)
    from_addr = email_cfg.get("from", "cribl-portal@noreply.local")
    approvers = email_cfg.get("approvers", [])
    portal_base = email_cfg.get("portal_base_url", "https://localhost:5000")

    if not smtp_host or not approvers:
        log.warning("send_approval_email — SMTP not configured, skipping")
        return {"sent": False, "reason": "SMTP not configured"}

    approve_url = f"{portal_base}/cribl/portal/api/approve/{request_id}?action=approve"
    reject_url  = f"{portal_base}/cribl/portal/api/approve/{request_id}?action=reject"

    msg = EmailMessage()
    msg["Subject"] = f"[Cribl Onboarding] Approval Required — {apmid} ({appname})"
    msg["From"] = from_addr
    msg["To"] = ", ".join(approvers)
    msg.set_content(
        f"A new container onboarding request requires approval.\n\n"
        f"  Request ID : {request_id}\n"
        f"  APM ID     : {apmid}\n"
        f"  App Name   : {appname}\n\n"
        f"  Approve    : {approve_url}\n"
        f"  Reject     : {reject_url}\n"
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
            s.send_message(msg)
        log.info("send_approval_email — sent to %s for %s", approvers, request_id)
        return {"sent": True, "to": approvers}
    except Exception as exc:
        log.error("send_approval_email — failed: %s", exc)
        return {"sent": False, "error": str(exc)}
```

### 5.2 New Routes (~50 lines)

#### `POST /cribl/portal/api/approve/<request_id>`

Admin-only. Called when approver clicks email link or uses the portal UI.

```python
@app.route("/cribl/portal/api/approve/<request_id>", methods=["POST", "GET"])
@admin_required
def approve_container(request_id):
    action = request.args.get("action", "approve")

    if action == "reject":
        config = load_config()
        portal_update_status_internal(request_id, "rejected", config)
        return jsonify({"request_id": request_id, "status": "rejected"})

    # Fetch the original request from ES to get apmid/region
    config = load_config()
    ds = config.get("datastream", {})
    es_sess, es_base_url, es_headers = _make_es_session_for_catalog(ds)
    index = ds.get("index", "logs-cribl-onboarding-requests")

    resp = es_sess.post(
        f"{es_base_url}/{index}/_search",
        json={"query": {"term": {"request_id.keyword": request_id}}, "size": 1},
        headers=es_headers, timeout=30,
    )
    hits = resp.json().get("hits", {}).get("hits", [])
    if not hits:
        return jsonify({"errors": [f"Request {request_id} not found"]}), 404

    doc = hits[0]["_source"]
    apmid   = doc["apmid"]
    region  = doc["region"]
    appname = doc.get("appname", "")
    env     = "prod"  # or derive from workspace selection

    # 1. Append to container registry
    reg_result = append_container_registry(apmid, region, env, config)

    # 2. Git commit + push (triggers Harness via webhook)
    git_result = git_commit_container(apmid, config)

    # 3. Update status
    portal_update_status_internal(request_id, "provisioning", config)

    return jsonify({
        "request_id": request_id,
        "status": "provisioning",
        "registry": reg_result,
        "git": git_result,
    })
```

#### `POST /cribl/api/webhooks/container-created`

Callback from Harness after container is created.

```python
@app.route("/cribl/api/webhooks/container-created", methods=["POST"])
def container_created_webhook():
    data = request.get_json(silent=True) or {}
    request_id = (data.get("request_id") or "").strip()
    apmid      = (data.get("apmid") or "").strip()
    status     = data.get("status", "success")

    # Optional: verify webhook secret
    webhook_secret = load_config().get("harness", {}).get("webhook_secret", "")
    if webhook_secret:
        provided = request.headers.get("X-Webhook-Secret", "")
        if provided != webhook_secret:
            return jsonify({"errors": ["Invalid webhook secret"]}), 403

    config = load_config()

    if status == "success" and request_id:
        portal_update_status_internal(request_id, "done", config)
        _catalog_cache["data"] = None
        _catalog_cache["ts"]   = 0.0
        log.info("container webhook — %s marked done", request_id)
    elif status == "failure" and request_id:
        portal_update_status_internal(request_id, "failed", config)
        log.error("container webhook — %s failed: %s", request_id, data.get("error"))

    return jsonify({"ok": True})
```

### 5.3 Modify `portal_submit()` (line 672)

Change the initial status and add email notification after ES index:

```python
# BEFORE (line 723):
"status": "pending",

# AFTER:
"status": "awaiting_approval",

# AFTER es_index (line 731), add:
email_result = send_approval_email(request_id, app_id, app_name, config)
```

### 5.4 Update Status Validation (line 756)

```python
# BEFORE:
if status not in ("pending", "done", "rejected"):

# AFTER:
if status not in ("pending", "awaiting_approval", "provisioning", "done", "rejected", "failed"):
```

---

## 6. Config Additions (`config.json`)

Add these blocks to `config.json` (and `config.example.json`):

```json
{
  "gitops": {
    "repo_dir": ".",
    "branch": "main",
    "container_registry_file": "azure_blob_account_containers.json"
  },
  "notifications": {
    "email": {
      "smtp_host": "smtp.yourorg.com",
      "smtp_port": 25,
      "from": "cribl-portal@yourorg.com",
      "portal_base_url": "https://portal.yourorg.com",
      "approvers": ["ops-team@yourorg.com", "platform-lead@yourorg.com"]
    }
  },
  "harness": {
    "trigger_url": "https://app.harness.io/gateway/pipeline/api/webhook/custom/...",
    "webhook_secret": "",
    "api_key": ""
  }
}
```

---

## 7. Harness Pipeline

The Harness pipeline is triggered by a GitHub webhook on push to `main` when `azure_blob_account_containers.json` changes.

### Pipeline stages:

```yaml
pipeline:
  name: Azure Container Provisioning
  stages:
    - stage: DetectChanges
      steps:
        - step: DiffContainers
          type: ShellScript
          spec:
            shell: Bash
            command: |
              git diff HEAD~1 -- azure_blob_account_containers.json \
                | grep '^\+.*"containers"' || echo "no new containers"

    - stage: CreateContainers
      steps:
        - step: ProvisionContainers
          type: ShellScript
          spec:
            shell: Bash
            command: |
              # Parse new containers from the registry file
              NEW_CONTAINERS=$(python3 -c "
              import json, sys
              with open('azure_blob_account_containers.json') as f:
                  reg = json.load(f)
              for acct, info in reg['storage_accounts'].items():
                  for c in info['containers']:
                      print(f\"{acct},{c}\")
              ")

              for line in $NEW_CONTAINERS; do
                ACCT=$(echo $line | cut -d, -f1)
                CONTAINER=$(echo $line | cut -d, -f2)
                echo "Creating container $CONTAINER in $ACCT..."
                az storage container create \
                  --account-name "$ACCT" \
                  --name "$CONTAINER" \
                  --auth-mode login \
                  2>/dev/null && echo "  OK (created or exists)" || echo "  FAILED"
              done

    - stage: Notify
      steps:
        - step: CallbackToPortal
          type: ShellScript
          spec:
            shell: Bash
            command: |
              curl -X POST "$PORTAL_URL/cribl/api/webhooks/container-created" \
                -H "Content-Type: application/json" \
                -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
                -d "{\"request_id\": \"$REQUEST_ID\", \"apmid\": \"$APMID\", \"status\": \"success\"}"
```

### Passing request context to Harness

The git commit message contains the apmid. Harness can parse it:

```bash
APMID=$(git log -1 --pretty=%s | sed 's/onboard: add container for //')
```

For `request_id`, two options:
- **Option A**: Include it in the commit message: `"onboard: add container for {apmid} ref={request_id}"`
- **Option B**: Query the portal ES index by apmid to get the latest request_id

---

## 8. Decisions Needed Before Implementation

| # | Decision | Options | Notes |
|---|----------|---------|-------|
| 1 | **Approval gate location** | (a) Portal email → `/approve` route | (b) Harness approval stage | Option (a) gives portal full control; (b) uses Harness built-in approvals |
| 2 | **Git authentication** | (a) Deploy key (write) | (b) GitHub App token | (c) PAT in config | Deploy key is simplest; GitHub App is most secure |
| 3 | **Email provider** | (a) Internal SMTP relay | (b) SendGrid/SES API | SMTP is zero-dependency; API needs `sendgrid` pip package |
| 4 | **Environment derivation** | (a) Always `prod` | (b) Derive from workspace | (c) User selects on form | Workspace already maps to env in config |
| 5 | **Idempotency** | `az storage container create` is already idempotent | No special handling needed | Harness re-runs are safe |
| 6 | **Request ID → Harness** | (a) In commit message | (b) Query ES from Harness | Option (a) is simpler, no extra API call |
| 7 | **Multi-region** | Create in both azn+azs or just selected region? | Portal form already captures region | Could do both if needed |

---

## 9. Files Changed Summary

| File | Change Type | Lines (est.) |
|------|-------------|-------------|
| `app.py` | Modified — 3 new functions, 2 new routes, 2 small edits | +150 |
| `azure_blob_account_containers.json` | New file — container registry | ~30 |
| `config.example.json` | Modified — add gitops/notifications/harness blocks | +20 |
| `.gitignore` | Verify `config.json` is ignored but `azure_blob_account_containers.json` is NOT | ~1 |
| `requirements.txt` | No change — `smtplib` is stdlib | 0 |

---

## 10. Testing Checklist

- [ ] Submit onboarding request → status = `awaiting_approval`, email sent
- [ ] Approve via email link → container JSON updated, git push succeeds, status = `provisioning`
- [ ] Reject via email link → status = `rejected`, no git commit
- [ ] Duplicate apmid → idempotent (not re-added to JSON)
- [ ] Harness callback (success) → status = `done`, catalog cache busted
- [ ] Harness callback (failure) → status = `failed`
- [ ] Webhook secret validation → 403 on invalid secret
- [ ] Catalog page shows new status values correctly
- [ ] Dry-run mode → no git commit, no email (existing pattern)
- [ ] Missing SMTP config → graceful skip with log warning
