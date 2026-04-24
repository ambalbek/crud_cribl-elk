#!/usr/bin/env python3
"""
app.py — Unified Cribl Framework

Combines:
  - cribl-flask  (Cribl Pusher + ELK Roles + Cribl Routes)
  - cribl-portal (Client-facing onboarding request portal)

Run with:
    flask run --host=0.0.0.0 --port=5000
  or:
    python app.py

Environment variables:
    LOG_LEVEL   DEBUG / INFO / WARNING / ERROR  (default: INFO)
    LOG_FILE    Path to log file  (default: none, console only)
"""
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from functools import wraps
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import requests as http_client
import urllib3
from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from requests.auth import HTTPBasicAuth
from werkzeug.exceptions import HTTPException

from cribl_config import (
    get_dest_prefix, get_dest_template_path, get_route_template_path,
    get_workspace, build_workspace_urls,
)
from cribl_utils import read_json, read_apps_from_file
from otel_setup import configure_otel, make_json_formatter, use_json_logging

configure_otel("cribl-framework")

SCRIPT_DIR  = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.json"
PUSHER      = SCRIPT_DIR / "cribl-pusher.py"
ROLE_RM     = SCRIPT_DIR / "role_rm.py"

# Microservice base URLs — set via env in docker-compose; fall back to empty
# so the portal degrades gracefully to subprocess mode when running standalone.
CRIBL_SERVICE_URL = os.environ.get("CRIBL_SERVICE_URL", "").rstrip("/")
ECE_SERVICE_URL   = os.environ.get("ECE_SERVICE_URL",   "").rstrip("/")


# ── Logging setup ──────────────────────────────────────────────────────────────

def setup_app_logging(app: Flask) -> logging.Logger:
    """
    Configure a dedicated 'cribl-framework' logger for the web layer.

    - Console handler always attached (stdout).
    - File handler attached when LOG_FILE env var is set
      (daily rotation, 30-day retention).
    - Flask's default werkzeug request logger is left intact but its
      level is raised to WARNING so it doesn't double-print every request.
    """
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        log_level = "INFO"

    if use_json_logging():
        formatter = make_json_formatter("cribl-framework")
    else:
        fmt       = "%(asctime)s  %(levelname)-8s  [framework]  %(message)s"
        datefmt   = "%Y-%m-%d %H:%M:%S"
        formatter = logging.Formatter(fmt, datefmt)

    logger = logging.getLogger("cribl-framework")
    logger.setLevel(getattr(logging, log_level))
    logger.handlers.clear()
    logger.propagate = False

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File (optional)
    log_file = os.environ.get("LOG_FILE", "").strip()
    if log_file:
        fh = TimedRotatingFileHandler(
            log_file, when="midnight", backupCount=30, encoding="utf-8"
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        logger.info("File logging enabled: %s", log_file)

    # Silence werkzeug's per-request lines (we log our own)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    return logger


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

log = setup_app_logging(app)

try:
    from opentelemetry.instrumentation.flask import FlaskInstrumentor
    FlaskInstrumentor().instrument_app(app)
except ImportError:
    pass


# ── Request lifecycle hooks ────────────────────────────────────────────────────

@app.context_processor
def inject_user():
    """Make current user info available in all templates."""
    return {
        "current_user": session.get("username"),
        "current_role": session.get("role"),
        "current_display_name": session.get("display_name"),
    }


@app.before_request
def _before():
    g.start_time = time.monotonic()
    g.username = session.get("username")
    g.role = session.get("role")
    log.info("→ %s %s  [%s]  user=%s", request.method, request.path,
             request.remote_addr or "-", g.username or "anonymous")


@app.after_request
def _after(response):
    elapsed_ms = (time.monotonic() - g.start_time) * 1000
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    log.log(level, "← %s %s  %d  %.0fms",
            request.method, request.path,
            response.status_code, elapsed_ms)
    return response


# ── Unhandled exception handler — always return JSON, never bare HTML ──────────

@app.errorhandler(404)
def _not_found(exc):
    log.warning("404 Not Found: %s %s", request.method, request.path)
    return jsonify({"errors": [f"Not found: {request.path}"]}), 404


@app.errorhandler(Exception)
def _handle_exception(exc):
    # Let Flask handle standard HTTP errors (404, 405, etc.) normally
    if isinstance(exc, HTTPException):
        return exc

    if isinstance(exc, SystemExit):
        # sys.exit() called inside a route (e.g. cribl die()) — treat as 500
        msg = f"Internal process exited unexpectedly (code={exc.code})"
    else:
        msg = str(exc)

    log.error("Unhandled exception on %s %s:\n%s",
              request.method, request.path,
              traceback.format_exc())
    return jsonify({"errors": [f"Server error: {msg}"]}), 500


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ── Session configuration ─────────────────────────────────────────────────────

_startup_config = load_config()
app.secret_key = _startup_config.get("secret_key", "CHANGE-ME-insecure-default")
app.config["SESSION_COOKIE_NAME"] = "cribl_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
_auth_cfg = _startup_config.get("auth", {})
app.permanent_session_lifetime = timedelta(
    minutes=_auth_cfg.get("session_lifetime_minutes", 480)
)


def local_authenticate(username, password):
    """Check local_admins and local_users accounts."""
    config = load_config()
    auth = config.get("auth", {})
    for admin in auth.get("local_admins", []):
        if admin.get("username") == username and admin.get("password") == password:
            log.info("Local admin auth OK — user=%s", username)
            return True, "admin", admin.get("display_name", username)
    for local_user in auth.get("local_users", []):
        if local_user.get("username") == username and local_user.get("password") == password:
            log.info("Local user auth OK — user=%s", username)
            return True, "user", local_user.get("display_name", username)
    return False, None, "Invalid credentials."


# ── Auth decorators ───────────────────────────────────────────────────────────

def login_required(f):
    """Redirect to login if no valid session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require admin role. Redirects to login or returns 403."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login_page", next=request.path))
        if session.get("role") != "admin":
            log.warning("Unauthorized admin access attempt by %s to %s",
                        session.get("username"), request.path)
            return render_template("login.html",
                                   error="You do not have permission to access this page."), 403
        return f(*args, **kwargs)
    return decorated


def run_subprocess(cmd: list, masked: str = "") -> tuple:
    log.info("  subprocess: %s", masked or " ".join(cmd))
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(SCRIPT_DIR),
    )
    log.info("  subprocess exit code: %d", result.returncode)
    if result.returncode != 0:
        log.warning("  subprocess failed — first 500 chars: %s",
                    (result.stdout or "")[:500])
    return result.stdout or "", result.returncode


def mask_cmd(cmd: list, sensitive: set) -> str:
    masked = [
        "***" if i > 0 and cmd[i - 1] in sensitive else part
        for i, part in enumerate(cmd)
    ]
    return " ".join(masked)


# ── Portal helpers ─────────────────────────────────────────────────────────────

def es_index(doc: dict, config: dict) -> str:
    """Write a document to the configured ES datastream. Returns the ES _id."""
    ds       = config.get("datastream", {})
    base_url = ds.get("elk_url", "").strip().rstrip("/")
    index    = ds.get("index", "logs-cribl-onboarding-requests")
    skip_ssl = ds.get("skip_ssl", False)
    timeout  = ds.get("timeout", 30)

    if not base_url:
        raise ValueError(
            'datastream.elk_url is not configured in config.json. '
            'Example: "datastream": { "elk_url": "https://localhost:9200", "index": "cribl-onboarding-requests", ... }'
        )

    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
        log.debug("elk_url had no scheme — prepended https://: %s", base_url)

    if skip_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = http_client.Session()
    session.verify = not skip_ssl

    headers = {"Content-Type": "application/json"}
    token    = ds.get("token",    "").strip()
    username = ds.get("username", "").strip()
    password = ds.get("password", "").strip()
    if token:
        headers["Authorization"] = f"ApiKey {token}"
    elif username:
        session.auth = (username, password)

    resp = session.post(
        f"{base_url}/{index}/_doc",
        json=doc,
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("_id", "unknown")


def portal_update_status_internal(request_id: str, status: str, config: dict) -> dict:
    """
    Update the status of a portal request directly in Elasticsearch.
    Used by cribl-pusher after a successful run to mark requests as done.
    """
    ds       = config.get("datastream", {})
    base_url = ds.get("elk_url", "").strip().rstrip("/")
    index    = ds.get("index", "logs-cribl-onboarding-requests")
    skip_ssl = ds.get("skip_ssl", False)
    timeout  = ds.get("timeout", 30)

    if not base_url:
        log.warning("portal_update_status — datastream.elk_url not configured; skipping")
        return {"skipped": True, "reason": "datastream not configured"}

    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url

    if skip_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    headers = {"Content-Type": "application/json"}
    token    = ds.get("token",    "").strip()
    username = ds.get("username", "").strip()
    password = ds.get("password", "").strip()
    if token:
        headers["Authorization"] = f"ApiKey {token}"

    session = http_client.Session()
    session.verify = not skip_ssl
    if not token and username:
        session.auth = (username, password)

    payload = {
        "query":  {"term": {"request_id.keyword": request_id}},
        "script": {
            "source": "ctx._source.status = params.status",
            "lang": "painless",
            "params": {"status": status},
        },
    }

    try:
        resp = session.post(
            f"{base_url}/{index}/_update_by_query",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        if resp.status_code == 200:
            result  = resp.json()
            updated = result.get("updated", 0)
            log.info("portal_update_status — request_id=%s  status=%s  updated=%d",
                     request_id, status, updated)
            return {"ok": True, "updated": updated}
        else:
            log.warning("portal_update_status — %d %s", resp.status_code, resp.text[:200])
            return {"ok": False, "status_code": resp.status_code, "body": resp.text[:500]}
    except Exception as exc:
        log.error("portal_update_status — failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ── Entitlement helpers ───────────────────────────────────────────────────────

def extract_entitlement_cns(rules, filter_text):
    """
    Walk the role mapping rules tree and extract DNs that contain the entitlement filter.
    Elasticsearch role mapping rules can have nested all/any/except/field structures.
    """
    cns = set()

    def walk(node):
        if not node or not isinstance(node, dict):
            return
        if 'field' in node:
            for field_key in ('dn', 'groups'):
                values = node['field'].get(field_key)
                if values is None:
                    continue
                if isinstance(values, str):
                    values = [values]
                for v in values:
                    if filter_text.lower() in v.lower():
                        cns.add(v)
        for key in ('all', 'any'):
            if key in node and isinstance(node[key], list):
                for child in node[key]:
                    walk(child)
        if 'except' in node:
            walk(node['except'])

    walk(rules)
    return list(cns)


def parse_cn(dn):
    """Extract the CN value from a full DN string."""
    match = re.search(r'CN=([^,]+)', dn, re.IGNORECASE)
    return match.group(1) if match else dn


def fetch_role_mappings(cluster):
    """Fetch role mappings from an Elasticsearch cluster via the Security API."""
    url = f"{cluster['url'].rstrip('/')}/_security/role_mapping"
    log.info("Cluster [%s] — requesting %s", cluster['name'], url)
    start = time.time()
    try:
        resp = http_client.get(
            url,
            auth=HTTPBasicAuth(cluster['username'], cluster['password']),
            verify=False,
            timeout=(10, 120),
        )
        elapsed = time.time() - start
        log.info("Cluster [%s] — %d response in %.2fs", cluster['name'], resp.status_code, elapsed)
        resp.raise_for_status()
        return resp.json()
    except http_client.exceptions.ConnectTimeout:
        log.error("Cluster [%s] — connection timed out after %.2fs to %s", cluster['name'], time.time() - start, url)
        raise
    except http_client.exceptions.ReadTimeout:
        log.error("Cluster [%s] — read timed out after %.2fs to %s", cluster['name'], time.time() - start, url)
        raise
    except http_client.exceptions.ConnectionError as e:
        log.error("Cluster [%s] — connection error after %.2fs: %s", cluster['name'], time.time() - start, e)
        raise
    except http_client.exceptions.RequestException as e:
        log.error("Cluster [%s] — request failed after %.2fs: %s", cluster['name'], time.time() - start, e)
        raise


# ── Microservice HTTP helpers ─────────────────────────────────────────────────

def _svc_post(base_url: str, path: str, **kwargs) -> tuple[dict, int]:
    """
    POST to an internal microservice.  Returns (json_body, status_code).
    Raises RuntimeError if the service URL is not configured.
    """
    if not base_url:
        raise RuntimeError("Service URL is not configured")
    url = base_url + path
    resp = http_client.post(url, timeout=120, **kwargs)
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}
    return body, resp.status_code


# ── Command builders (for subprocess calls to CLI scripts) ─────────────────────

def build_pusher_cmd(form: dict, appfile_path: str) -> tuple:
    cmd = [
        sys.executable, str(PUSHER),
        "--yes",
        "--workspace",    form["workspace"],
        "--worker-group", form["worker_group"],
        "--region",       form["region"],
        "--log-level",    form.get("log_level", "INFO"),
        "--config",       str(CONFIG_PATH),
    ]

    if form.get("cribl_url", "").strip():
        cmd += ["--cribl-url", form["cribl_url"].strip()]
    if form.get("allow_prod"):
        cmd.append("--allow-prod")
    if form.get("dry_run"):
        cmd.append("--dry-run")
    if form.get("skip_ssl"):
        cmd.append("--skip-ssl")

    token    = form.get("token", "").strip()
    username = form.get("username", "").strip()
    password = form.get("password", "").strip()
    if token:
        cmd += ["--token", token]
    elif username and password:
        cmd += ["--username", username, "--password", password]

    if form.get("mode") == "bulk":
        cmd += ["--from-file", "--appfile", appfile_path or ""]
    else:
        cmd += ["--appid",   form.get("appid", "").strip(),
                "--appname", form.get("appname", "").strip()]

    group_id = form.get("group_id", "").strip()
    if group_id:
        cmd += ["--group-id", group_id]
        if form.get("create_missing_group"):
            cmd.append("--create-missing-group")
        if form.get("group_name", "").strip():
            cmd += ["--group-name", form["group_name"].strip()]

    if form.get("min_routes", "").strip():
        cmd += ["--min-existing-total-routes", form["min_routes"].strip()]
    if form.get("diff_lines", "").strip():
        cmd += ["--diff-lines", form["diff_lines"].strip()]
    if form.get("snapshot_dir", "").strip():
        cmd += ["--snapshot-dir", form["snapshot_dir"].strip()]
    if form.get("log_file", "").strip():
        cmd += ["--log-file", form["log_file"].strip()]

    sensitive = {"--password", "--token"}
    return cmd, mask_cmd(cmd, sensitive)


def build_rode_rm_cmd(form: dict, appfile_path: str) -> tuple:
    cmd = [sys.executable, str(ROLE_RM), "--yes", "--config", str(CONFIG_PATH)]

    if form.get("mode") == "bulk":
        cmd += ["--from-file", "--appfile", appfile_path or ""]
    else:
        cmd += ["--app_name", form.get("app_name", "").strip(),
                "--apmid",    form.get("apmid", "").strip()]

    cribl_token    = form.get("cribl_token", "").strip()
    cribl_username = form.get("cribl_username", "").strip()
    cribl_password = form.get("cribl_password", "").strip()
    if cribl_token:
        cmd += ["--token", cribl_token]
    elif cribl_username and cribl_password:
        cmd += ["--username", cribl_username, "--password", cribl_password]

    skip_elk = bool(form.get("skip_elk"))
    if not skip_elk:
        cmd += ["--elk-url", form.get("elk_url_nonprod", "").strip()]
        np_token = form.get("elk_token_nonprod", "").strip()
        np_user  = form.get("elk_user_nonprod", "").strip()
        np_pass  = form.get("elk_password_nonprod", "").strip()
        if np_token:
            cmd += ["--elk-token", np_token]
        elif np_user:
            cmd += ["--elk-user", np_user]
            if np_pass:
                cmd += ["--elk-password", np_pass]

        cmd += ["--elk-url-prod", form.get("elk_url_prod", "").strip()]
        p_token = form.get("elk_token_prod", "").strip()
        p_user  = form.get("elk_user_prod", "").strip()
        p_pass  = form.get("elk_password_prod", "").strip()
        if p_token:
            cmd += ["--elk-token-prod", p_token]
        elif p_user:
            cmd += ["--elk-user-prod", p_user]
            if p_pass:
                cmd += ["--elk-password-prod", p_pass]

    if form.get("cribl_url", "").strip():
        cmd += ["--cribl-url", form["cribl_url"].strip()]
    cmd += ["--workspace", form.get("workspace", "")]
    if form.get("worker_group", "").strip():
        cmd += ["--worker-group", form["worker_group"].strip()]
    if form.get("region", "").strip():
        cmd += ["--region", form["region"].strip()]
    if form.get("allow_prod"):
        cmd.append("--allow-prod")
    cmd += ["--order", form.get("order", "elk-first")]
    if skip_elk:
        cmd.append("--skip-elk")
    if form.get("skip_cribl"):
        cmd.append("--skip-cribl")
    if form.get("dry_run"):
        cmd.append("--dry-run")
    if form.get("skip_ssl"):
        cmd.append("--skip-ssl")
    cmd += ["--log-level", form.get("log_level", "INFO")]

    sensitive = {
        "--elk-password", "--elk-token",
        "--elk-password-prod", "--elk-token-prod",
        "--password", "--token",
    }
    return cmd, mask_cmd(cmd, sensitive)


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES — Authentication
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/cribl/login", methods=["GET"])
def login_page():
    if session.get("username"):
        return redirect(url_for("landing"))
    return render_template("login.html")


@app.route("/cribl/login", methods=["POST"])
def login_submit():
    """Handle local fallback login (username/password from config)."""
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    next_url = request.form.get("next") or request.args.get("next") or "/"

    if not username or not password:
        return render_template("login.html",
                               error="Username and password are required.", next=next_url)

    success, role, display_name_or_error = local_authenticate(username, password)

    if not success:
        log.warning("Local login failed for user=%s from %s", username, request.remote_addr)
        return render_template("login.html",
                               error=display_name_or_error, next=next_url)

    session.permanent = True
    session["username"] = username
    session["role"] = role
    session["display_name"] = display_name_or_error

    log.info("Local login OK — user=%s role=%s from %s", username, role, request.remote_addr)

    user_allowed = ("/cribl/portal", "/cribl/portal/", "/cribl/portal/api/submit", "/cribl/api/submit",
                    "/cribl/entitlements", "/cribl/entitlements/", "/cribl/api/entitlements")
    if role == "user" and next_url not in user_allowed:
        next_url = "/cribl/portal"

    return redirect(next_url)


@app.route("/cribl/logout")
def logout():
    username = session.get("username", "anonymous")
    session.clear()
    log.info("Logout — user=%s from %s", username, request.remote_addr)
    return redirect(url_for("login_page"))


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES — Landing / Navigation
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/cribl/")
@login_required
def landing():
    if session.get("role") == "user":
        return redirect(url_for("portal_index"))
    return render_template("index.html")


@app.route("/cribl/health")
def health():
    return "ok", 200


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES — Portal (onboarding requests)
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/cribl/portal")
@app.route("/cribl/portal/")
@login_required
def portal_index():
    config = load_config()
    return render_template("request.html", iiq_url=config.get("iiq_url", ""))


@app.route("/cribl/portal/api/submit", methods=["POST"])
@app.route("/cribl/api/submit", methods=["POST"])
@login_required
def portal_submit():
    log.debug("submit — Content-Type: %s  body: %s",
              request.content_type, request.get_data(as_text=True)[:500])

    data     = request.get_json(silent=True) or {}
    lan_id   = session.get("username", (data.get("lan_id") or "").strip())
    req_name = session.get("display_name", (data.get("requester_name") or "").strip())
    app_id   = (data.get("apmid")    or "").strip()
    app_name = (data.get("appname")  or "").strip()
    region   = (data.get("region")   or "").strip()
    log_dests = [d for d in (data.get("log_destinations") or []) if d]
    log_types = [t for t in (data.get("log_types") or []) if t]
    groups   = [grp for grp in (data.get("groups") or []) if grp]

    log.info("submit — lan_id=%r  requester_name=%r  apmid=%r  appname=%r  region=%r  log_dest=%s  log_types=%s  groups=%s",
             lan_id, req_name, app_id, app_name, region, log_dests, log_types, groups)

    errors = []
    if not app_id:                        errors.append("APM ID is required.")
    if not app_name:                      errors.append("App Name is required.")
    elif not re.match(r"^\w+$", app_name):
                                          errors.append("App Name must be a single word using only letters, numbers, and underscores.")
    if region not in ("azn", "azs"):      errors.append("Region must be azn or azs.")
    if not log_dests:                     errors.append("Select at least one log destination.")
    if not log_types:                     errors.append("Select at least one log type.")
    if not groups:                        errors.append("Select at least one entitlement group.")
    if errors:
        return jsonify({"errors": errors}), 400

    try:
        config = load_config()
    except Exception as exc:
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    now        = datetime.now(timezone.utc)
    request_id = f"REQ-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

    doc = {
        "@timestamp":         now.isoformat(),
        "request_id":         request_id,
        "lan_id":             lan_id,
        "requester_name":     req_name,
        "apmid":              app_id,
        "appname":            app_name,
        "region":             region,
        "log_destinations":   log_dests,
        "log_types":          log_types,
        "entitlement_groups": groups,
        "status":             "pending",
    }

    try:
        log.info("indexing to ES — index=%s  request_id=%s",
                 config.get("datastream", {}).get("index", "logs-cribl-onboarding-requests"),
                 request_id)
        es_id = es_index(doc, config)
        log.info("ES index OK — request_id=%s  es_id=%s", request_id, es_id)
    except Exception as exc:
        log.error("ES index failed — %s: %s", type(exc).__name__, exc)
        return jsonify({"errors": [f"Failed to store request: {exc}"]}), 500

    return jsonify({"request_id": request_id})


@app.route("/cribl/portal/admin/update-status", methods=["GET", "POST"])
@app.route("/cribl/admin/update-status", methods=["GET", "POST"])
@admin_required
def portal_admin_update_status():
    if request.method == "GET":
        return render_template("admin.html")
    try:
        config = load_config()
    except Exception as exc:
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    data       = request.get_json(silent=True) or {}
    request_id = (data.get("request_id") or "").strip()
    status     = (data.get("status")     or "").strip()

    if not request_id:
        return jsonify({"errors": ["request_id is required"]}), 400
    if status not in ("pending", "done", "rejected"):
        return jsonify({"errors": ["status must be one of: pending, done, rejected"]}), 400

    ds       = config.get("datastream", {})
    base_url = ds.get("elk_url", "").strip().rstrip("/")
    index    = ds.get("index", "logs-cribl-onboarding-requests")
    skip_ssl = ds.get("skip_ssl", False)
    timeout  = ds.get("timeout", 30)

    if not base_url:
        return jsonify({"errors": ["datastream.elk_url is not configured in config.json"]}), 500

    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url

    if skip_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    headers = {"Content-Type": "application/json"}
    token    = ds.get("token",    "").strip()
    username = ds.get("username", "").strip()
    password = ds.get("password", "").strip()
    if token:
        headers["Authorization"] = f"ApiKey {token}"

    session = http_client.Session()
    session.verify = not skip_ssl
    if not token and username:
        session.auth = (username, password)

    payload = {
        "query":  {"term": {"request_id.keyword": request_id}},
        "script": {
            "source": "ctx._source.status = params.status",
            "lang": "painless",
            "params": {"status": status},
        },
    }

    try:
        resp = session.post(
            f"{base_url}/{index}/_update_by_query",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        result  = resp.json()
        updated = result.get("updated", 0)
        if updated == 0:
            log.warning("admin/update-status — request_id=%s not found", request_id)
            return jsonify({"errors": [f"Request ID {request_id!r} not found"]}), 404
        log.info("admin/update-status — request_id=%s  status=%s  updated=%d", request_id, status, updated)
        return jsonify({"request_id": request_id, "status": status, "updated": updated})
    except Exception as exc:
        log.error("admin/update-status failed — %s: %s", type(exc).__name__, exc)
        return jsonify({"errors": [f"Failed to update status: {exc}"]}), 500


@app.route("/cribl/health/es")
def health_es():
    try:
        config  = load_config()
        ds      = config.get("datastream", {})
        base_url = ds.get("elk_url", "").strip().rstrip("/")
        skip_ssl = ds.get("skip_ssl", False)
        timeout  = ds.get("timeout", 30)

        if not base_url.startswith(("http://", "https://")):
            base_url = "https://" + base_url

        if skip_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        headers = {"Content-Type": "application/json"}
        token    = ds.get("token",    "").strip()
        username = ds.get("username", "").strip()
        password = ds.get("password", "").strip()
        if token:
            headers["Authorization"] = f"ApiKey {token}"

        session = http_client.Session()
        session.verify = not skip_ssl
        if not token and username:
            session.auth = (username, password)

        resp = session.get(f"{base_url}/_cluster/health", headers=headers, timeout=timeout)
        return jsonify({"status": "ok", "es_status": resp.status_code, "es_body": resp.json()}), 200
    except Exception as exc:
        log.error("ES health check failed: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES — Entitlement Lookup
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/cribl/entitlements")
@app.route("/cribl/entitlements/")
@login_required
def entitlements_page():
    return render_template("entitlements.html")


@app.route("/cribl/api/entitlements")
@login_required
def api_entitlements():
    try:
        config = load_config()
    except Exception as exc:
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    ent_cfg     = config.get("entitlement", {})
    clusters    = ent_cfg.get("clusters", [])
    filter_text = ent_cfg.get("entitlementFilter", "")

    if not clusters:
        return jsonify({"errors": ["No entitlement clusters configured in config.json"]}), 500

    log.info("Entitlements API called — filter: '%s', clusters: %d", filter_text, len(clusters))
    results = []

    for cluster in clusters:
        try:
            role_mappings = fetch_role_mappings(cluster)
            log.info("Cluster [%s] — %d role mappings returned", cluster['name'], len(role_mappings))

            for mapping_name, mapping in role_mappings.items():
                entitlement_dns = extract_entitlement_cns(
                    mapping.get('rules', {}), filter_text
                )
                for dn in entitlement_dns:
                    results.append({
                        'cluster': cluster['name'],
                        'mappingName': mapping_name,
                        'entitlement': parse_cn(dn),
                        'entitlementDN': dn,
                        'roles': mapping.get('roles', []),
                        'enabled': mapping.get('enabled', False),
                    })

        except Exception as e:
            log.exception("Cluster [%s] — failed: %s", cluster['name'], e)
            results.append({
                'cluster': cluster['name'],
                'mappingName': '-',
                'entitlement': f'ERROR: {str(e)}',
                'entitlementDN': '',
                'roles': [],
                'enabled': False,
                'error': True,
            })

    results.sort(key=lambda r: (r['cluster'], r['entitlement']))
    return jsonify(results)


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES — Cribl Pusher (automation UI)
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/cribl")
@app.route("/cribl/")
@admin_required
def cribl_landing():
    return render_template("index.html")


@app.route("/cribl/app")
@app.route("/cribl/app/")
@admin_required
def cribl_app_page():
    try:
        config = load_config()
    except Exception as exc:
        log.error("Failed to load config.json: %s", exc)
        return f"Error loading config.json: {exc}", 500
    workspaces = {
        k: v for k, v in config.get("workspaces", {}).items()
        if not k.startswith("_")
    }
    return render_template("app.html", workspaces=workspaces, config=config)


@app.route("/cribl/api/run-pusher", methods=["POST"])
@admin_required
def run_pusher():
    form       = request.form
    file       = request.files.get("appfile")
    mode       = form.get("mode", "single")
    request_id = (form.get("request_id") or "").strip()

    errors = []
    if mode == "single":
        if not form.get("appid", "").strip():   errors.append("App ID is required.")
        if not form.get("appname", "").strip(): errors.append("App Name is required.")
    else:
        if not file or not file.filename:
            errors.append("Please upload an app list file (.txt).")

    worker_groups = form.getlist("worker_groups")
    if not worker_groups:
        errors.append("Select at least one worker group.")

    try:
        config = load_config()
    except Exception as exc:
        log.error("Config load error: %s", exc)
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    ws_cfg = config.get("workspaces", {}).get(form.get("workspace", ""), {})
    if ws_cfg.get("require_allow") and not form.get("allow_prod"):
        errors.append(
            f"Workspace '{form.get('workspace')}' requires the "
            "'Allow production writes' checkbox."
        )

    if errors:
        log.warning("run-pusher validation failed: %s", errors)
        return jsonify({"errors": errors}), 400

    dry_run = bool(form.get("dry_run"))
    log.info("run-pusher  workspace=%s  wgs=%s  mode=%s  dry_run=%s",
             form.get("workspace"), worker_groups, mode, dry_run)

    # ── Build apps list ────────────────────────────────────────────────────
    if mode == "bulk":
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".txt", delete=False, dir=SCRIPT_DIR
            ) as tmp:
                file.save(tmp)
                tmp_path = tmp.name
            apps = read_apps_from_file(tmp_path)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    else:
        apps = [(form.get("appid", "").strip(), form.get("appname", "").strip())]

    # ── HTTP path: call cribl_service /provision ───────────────────────────
    if CRIBL_SERVICE_URL:
        workspace_name = form.get("workspace", "")
        region         = form.get("region", "")
        try:
            ws_cfg         = get_workspace(config, workspace_name)
            route_tmpl     = read_json(get_route_template_path(config, ws_cfg, region))
            dest_tmpl      = read_json(get_dest_template_path(config, ws_cfg, region))
            dest_prefix    = get_dest_prefix(config, ws_cfg, region)
            routes_table   = ws_cfg.get("routes_table", "default")
            fallback_pipe  = route_tmpl.get("pipeline") or "main"
        except Exception as exc:
            log.error("run-pusher config resolution failed: %s", exc)
            return jsonify({"errors": [f"Config error: {exc}"]}), 500

        apps_payload = [{"apmid": apmid, "app_name": app_name} for apmid, app_name in apps]
        all_output   = ""
        last_rc      = 0
        results      = []

        for wg in worker_groups:
            payload = {
                "apps":             apps_payload,
                "route_template":   route_tmpl,
                "dest_template":    dest_tmpl,
                "dest_prefix":      dest_prefix,
                "routes_table":     routes_table,
                "dry_run":          dry_run,
                "fallback_pipeline": fallback_pipe,
            }
            try:
                body, status = _svc_post(
                    CRIBL_SERVICE_URL,
                    f"/api/v1/m/{wg}/provision",
                    json=payload,
                )
            except Exception as exc:
                log.error("cribl_service provision failed wg=%s: %s", wg, exc)
                body, status = {"error": str(exc)}, 500

            rc = 0 if status < 400 else 1
            if rc != 0:
                last_rc = rc
            results.append({"wg": wg, "status": status, "result": body})
            sep = "=" * 60
            all_output += (
                f"\n{sep}\n Worker group: {wg}\n{sep}\n"
                + json.dumps(body, indent=2)
            )

        portal_result = None
        if last_rc == 0 and not dry_run and request_id:
            portal_result = portal_update_status_internal(request_id, "done", config)

        return jsonify({
            "output":        all_output.strip(),
            "returncode":    last_rc,
            "results":       results,
            "portal_update": portal_result,
        })

    # ── Subprocess fallback (no CRIBL_SERVICE_URL) ─────────────────────────
    tmp_path = None
    try:
        if mode == "bulk" and file:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".txt", delete=False, dir=SCRIPT_DIR
            ) as tmp:
                file.save(tmp)
                tmp_path = tmp.name

        all_output = ""
        last_rc    = 0
        commands   = []

        for wg in worker_groups:
            form_dict = form.to_dict()
            form_dict["worker_group"] = wg
            cmd, masked = build_pusher_cmd(form_dict, tmp_path or "")
            commands.append({"wg": wg, "cmd": masked})
            output, rc = run_subprocess(cmd, masked)
            all_output += f"\n{'='*60}\n Worker group: {wg}\n{'='*60}\n{output}"
            if rc != 0:
                last_rc = rc

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    portal_result = None
    if last_rc == 0 and not dry_run and request_id:
        portal_result = portal_update_status_internal(request_id, "done", config)

    return jsonify({
        "output":        all_output.strip(),
        "returncode":    last_rc,
        "commands":      commands,
        "portal_update": portal_result,
    })


@app.route("/cribl/api/run-rode-rm", methods=["POST"])
@admin_required
def run_rode_rm():
    form       = request.form
    file       = request.files.get("appfile")
    mode       = form.get("mode", "single")
    request_id = (form.get("request_id") or "").strip()

    errors    = []
    skip_elk   = bool(form.get("skip_elk"))
    skip_cribl = bool(form.get("skip_cribl"))

    if mode == "single":
        if not form.get("app_name", "").strip(): errors.append("App Name is required.")
        if not form.get("apmid", "").strip():    errors.append("App ID is required.")
    else:
        if not file or not file.filename:
            errors.append("Please upload an app list file (.txt).")

    if skip_elk and skip_cribl:
        errors.append("Nothing to do: both Skip ELK and Skip Cribl are checked.")

    if not skip_cribl and not form.get("worker_group", "").strip():
        errors.append("Worker Group is required when Cribl is not skipped.")

    if not skip_elk:
        if not form.get("elk_url_nonprod", "").strip():
            errors.append("ELK Nonprod URL is required.")
        if not form.get("elk_token_nonprod", "").strip() and not form.get("elk_user_nonprod", "").strip():
            errors.append("ELK Nonprod: provide User or Token.")
        if not form.get("elk_url_prod", "").strip():
            errors.append("ELK Prod URL is required.")
        if not form.get("elk_token_prod", "").strip() and not form.get("elk_user_prod", "").strip():
            errors.append("ELK Prod: provide User or Token.")

    try:
        config = load_config()
    except Exception as exc:
        log.error("Config load error: %s", exc)
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    ws_cfg = config.get("workspaces", {}).get(form.get("workspace", ""), {})
    if ws_cfg.get("require_allow") and not form.get("allow_prod"):
        errors.append(
            f"Workspace '{form.get('workspace')}' requires the "
            "'Allow production writes' checkbox."
        )

    if errors:
        log.warning("run-rode-rm validation failed: %s", errors)
        return jsonify({"errors": errors}), 400

    log.info("run-rode-rm  workspace=%s  wg=%s  mode=%s  skip_elk=%s  skip_cribl=%s  dry_run=%s",
             form.get("workspace"), form.get("worker_group"), mode,
             skip_elk, skip_cribl, bool(form.get("dry_run")))

    dry_run = bool(form.get("dry_run"))

    # ── Build apps list ────────────────────────────────────────────────────
    if mode == "bulk":
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".txt", delete=False, dir=SCRIPT_DIR
            ) as tmp:
                file.save(tmp)
                tmp_path = tmp.name
            bulk_apps = read_apps_from_file(tmp_path)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    else:
        bulk_apps = [(form.get("app_name", "").strip(), form.get("apmid", "").strip())]

    # ── HTTP path: delegate to microservices ───────────────────────────────
    if ECE_SERVICE_URL and not skip_elk:
        all_output = ""
        last_rc    = 0
        svc_results = []

        for app_name, apmid in bulk_apps:
            params = {
                "app_name": app_name,
                "apmid":    apmid,
                "dry_run":  str(dry_run).lower(),
            }
            try:
                body, status = _svc_post(
                    ECE_SERVICE_URL,
                    "/api/v1/roles/provision",
                    params=params,
                )
            except Exception as exc:
                log.error("ece_service provision failed app=%s: %s", apmid, exc)
                body, status = {"error": str(exc)}, 500

            rc = 0 if status < 400 else 1
            if rc != 0:
                last_rc = rc
            svc_results.append({"apmid": apmid, "app_name": app_name, "status": status, "result": body})
            all_output += f"\n[ELK] {app_name} ({apmid})\n" + json.dumps(body, indent=2)

        # Cribl part: still use subprocess when ECE_SERVICE_URL is set but
        # CRIBL_SERVICE_URL may not be (or Cribl is skipped).
        if not skip_cribl and CRIBL_SERVICE_URL:
            workspace_name = form.get("workspace", "")
            region         = form.get("region", "")
            wg             = form.get("worker_group", "").strip()
            try:
                ws_cfg        = get_workspace(config, workspace_name)
                route_tmpl    = read_json(get_route_template_path(config, ws_cfg, region))
                dest_tmpl     = read_json(get_dest_template_path(config, ws_cfg, region))
                dest_prefix   = get_dest_prefix(config, ws_cfg, region)
                routes_table  = ws_cfg.get("routes_table", "default")
                fallback_pipe = route_tmpl.get("pipeline") or "main"
            except Exception as exc:
                return jsonify({"errors": [f"Cribl config error: {exc}"]}), 500

            apps_payload = [{"apmid": apmid, "app_name": app_name} for app_name, apmid in bulk_apps]
            cribl_payload = {
                "apps": apps_payload, "route_template": route_tmpl,
                "dest_template": dest_tmpl, "dest_prefix": dest_prefix,
                "routes_table": routes_table, "dry_run": dry_run,
                "fallback_pipeline": fallback_pipe,
            }
            try:
                cbody, cstatus = _svc_post(CRIBL_SERVICE_URL, f"/api/v1/m/{wg}/provision", json=cribl_payload)
            except Exception as exc:
                cbody, cstatus = {"error": str(exc)}, 500

            if cstatus >= 400:
                last_rc = 1
            all_output += f"\n[CRIBL] worker_group={wg}\n" + json.dumps(cbody, indent=2)
        elif not skip_cribl:
            # Cribl subprocess fallback when CRIBL_SERVICE_URL not set
            tmp_path = None
            try:
                cmd, masked = build_rode_rm_cmd(form.to_dict(), "")
                cribl_output, crc = run_subprocess(cmd, masked)
                if crc != 0:
                    last_rc = crc
                all_output += f"\n[CRIBL subprocess]\n{cribl_output}"
            except Exception as exc:
                all_output += f"\n[CRIBL subprocess error] {exc}"
                last_rc = 1

        portal_result = None
        if last_rc == 0 and not dry_run and request_id:
            portal_result = portal_update_status_internal(request_id, "done", config)

        return jsonify({
            "output":        all_output.strip(),
            "returncode":    last_rc,
            "results":       svc_results,
            "portal_update": portal_result,
        })

    # ── Subprocess fallback (no ECE_SERVICE_URL, or skip_elk=True) ────────
    tmp_path = None
    try:
        if mode == "bulk" and file:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".txt", delete=False, dir=SCRIPT_DIR
            ) as tmp:
                file.save(tmp)
                tmp_path = tmp.name

        cmd, masked = build_rode_rm_cmd(form.to_dict(), tmp_path or "")
        output, rc  = run_subprocess(cmd, masked)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    portal_result = None
    if rc == 0 and not dry_run and request_id:
        portal_result = portal_update_status_internal(request_id, "done", config)

    return jsonify({
        "output":        output,
        "returncode":    rc,
        "command":       masked,
        "portal_update": portal_result,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES — Service Catalog
# ══════════════════════════════════════════════════════════════════════════════

# ── Catalog cache ──────────────────────────────────────────────────────────────
_catalog_cache: dict = {"data": None, "ts": 0.0}
_CATALOG_CACHE_TTL   = 60  # seconds


# ── Catalog helper functions ───────────────────────────────────────────────────

def _make_es_session_for_catalog(ds: dict):
    """
    Build a requests.Session for a datastream/entitlement ES config block.
    Returns (session, base_url, headers).
    """
    base_url = ds.get("elk_url", "").strip().rstrip("/")
    skip_ssl = ds.get("skip_ssl", False)
    if base_url and not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    if skip_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    headers = {"Content-Type": "application/json"}
    token    = ds.get("token",    "").strip()
    username = ds.get("username", "").strip()
    password = ds.get("password", "").strip()
    if token:
        headers["Authorization"] = f"ApiKey {token}"
    http_sess = http_client.Session()
    http_sess.verify = not skip_ssl
    if not token and username:
        http_sess.auth = (username, password)
    return http_sess, base_url, headers


def _unwrap_cribl_response(obj: dict) -> dict:
    """Unwrap Cribl GET /routes response from outer {items:[{...}]} wrapper."""
    if isinstance(obj, dict) and isinstance(obj.get("items"), list) and obj["items"]:
        if isinstance(obj["items"][0], dict) and any(
            k in obj["items"][0] for k in ("routes", "groups")
        ):
            return obj["items"][0]
    return obj


def _count_routes_for_app(routes_obj: dict, apmid: str, appname: str) -> tuple:
    """
    Count Cribl routes in a GET /routes response that belong to this app.
    Matches routes whose id or name contains the apmid or appname.
    Returns (count, dest, pipeline).
    """
    root          = _unwrap_cribl_response(routes_obj)
    apmid_lower   = apmid.lower()
    appname_lower = appname.lower()

    all_routes = []
    if isinstance(root.get("routes"), list):
        all_routes.extend(root["routes"])
    if isinstance(root.get("groups"), list):
        for g in root["groups"]:
            if isinstance(g, dict) and isinstance(g.get("routes"), list):
                all_routes.extend(g["routes"])

    matching = [
        r for r in all_routes
        if isinstance(r, dict) and (
            apmid_lower   in str(r.get("id",   "")).lower()
            or apmid_lower   in str(r.get("name", "")).lower()
            or appname_lower in str(r.get("id",   "")).lower()
            or appname_lower in str(r.get("name", "")).lower()
        )
    ]

    dest     = matching[0].get("output",   "")   if matching else ""
    pipeline = matching[0].get("pipeline", None) if matching else None
    return len(matching), dest, pipeline


def _fetch_ilm_tier_stateless(
    base_url: str,
    headers: dict,
    auth,
    verify: bool,
    appname: str,
) -> str:
    """
    Thread-safe ILM tier check — creates its own connection.
    Returns hot / warm / cold / frozen / tiered / none.
    """
    pattern = f"logs-{appname}-*"
    try:
        resp = http_client.get(
            f"{base_url}/{pattern}/_ilm/explain",
            headers=headers, auth=auth, verify=verify, timeout=5,
        )
        if resp.status_code != 200:
            return "none"
        phases = {
            info.get("phase")
            for info in resp.json().get("indices", {}).values()
            if info.get("phase")
        }
        if not phases:
            return "none"
        return "tiered" if len(phases) > 1 else phases.pop()
    except Exception:
        return "none"


def _build_catalog(config: dict) -> list:
    """Aggregate catalog data from Cribl + Elasticsearch into per-app records."""
    log.info("catalog — starting build")

    # ── 1. Onboarding docs (latest 500, dedup by apmid) ───────────────────────
    ds      = config.get("datastream", {})
    index   = ds.get("index",   "cribl-onboarding-requests")
    timeout = ds.get("timeout", 30)
    es_sess, es_base_url, es_headers = _make_es_session_for_catalog(ds)

    docs = []
    if es_base_url:
        try:
            resp = es_sess.post(
                f"{es_base_url}/{index}/_search",
                json={
                    "size": 500,
                    "sort": [{"@timestamp": {"order": "desc"}}],
                    "query": {"match_all": {}},
                },
                headers=es_headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            docs = [h["_source"] for h in resp.json().get("hits", {}).get("hits", [])]
            log.info("catalog — fetched %d onboarding docs", len(docs))
        except Exception as exc:
            log.warning("catalog — onboarding fetch failed: %s", exc)

    # Deduplicate: docs are sorted desc by timestamp, so first occurrence = latest
    seen: dict = {}
    for doc in docs:
        apmid = (doc.get("apmid") or "").strip()
        if apmid and apmid not in seen:
            seen[apmid] = doc

    if not seen:
        log.info("catalog — no apps in onboarding index; returning empty catalog")
        return []

    # ── 2. ELK role mappings from all entitlement clusters ────────────────────
    ent_cfg  = config.get("entitlement", {})
    clusters = ent_cfg.get("clusters", [])
    all_role_mappings: dict = {}
    for cluster in clusters:
        try:
            mappings = fetch_role_mappings(cluster)
            all_role_mappings.update(mappings)
        except Exception as exc:
            log.warning("catalog — role mapping fetch failed cluster=%s: %s",
                        cluster.get("name"), exc)

    # ── 3. Cribl routes for every workspace / worker group ────────────────────
    workspaces = {
        k: v for k, v in config.get("workspaces", {}).items()
        if not k.startswith("_")
    }
    creds_global = config.get("credentials", {})
    cribl_routes_cache: dict = {}  # (ws_name, wg) -> routes_obj

    for ws_name, ws_cfg in workspaces.items():
        worker_groups = ws_cfg.get("worker_groups", [])
        routes_table  = ws_cfg.get("routes_table", "default")
        root_url      = ws_cfg.get("base_url", config.get("base_url", "")).rstrip("/")
        if not root_url:
            continue

        skip_ssl = ws_cfg.get("skip_ssl", config.get("skip_ssl", False))
        ws_token = (ws_cfg.get("token", "") or creds_global.get("token", "")).strip()
        ws_user  = (ws_cfg.get("username", "") or creds_global.get("username", "")).strip()
        ws_pass  = (ws_cfg.get("password", "") or creds_global.get("password", "")).strip()

        ws_sess = http_client.Session()
        ws_sess.verify = not skip_ssl
        if skip_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        cribl_token = ws_token
        if not cribl_token and ws_user:
            try:
                auth_r = ws_sess.post(
                    f"{root_url}/api/v1/auth/login",
                    json={"username": ws_user, "password": ws_pass},
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    timeout=30,
                )
                if auth_r.status_code == 200:
                    cribl_token = auth_r.json().get("token", "")
                else:
                    log.warning("catalog — Cribl auth failed ws=%s: %d", ws_name, auth_r.status_code)
                    continue
            except Exception as exc:
                log.warning("catalog — Cribl auth error ws=%s: %s", ws_name, exc)
                continue

        if cribl_token:
            ws_sess.headers.update({"Authorization": f"Bearer {cribl_token}"})

        for wg in worker_groups:
            url = f"{root_url}/api/v1/m/{wg}/routes/{routes_table}"
            try:
                r = ws_sess.get(url, timeout=30)
                if r.status_code == 200:
                    cribl_routes_cache[(ws_name, wg)] = r.json()
                    log.info("catalog — routes fetched ws=%s wg=%s", ws_name, wg)
                else:
                    log.warning("catalog — routes %d ws=%s wg=%s", r.status_code, ws_name, wg)
            except Exception as exc:
                log.warning("catalog — routes error ws=%s wg=%s: %s", ws_name, wg, exc)

    # ── 4. ILM tier — parallel checks for all unique appnames ─────────────────
    unique_appnames = [
        (doc.get("appname") or "").strip()
        for doc in seen.values()
        if (doc.get("appname") or "").strip()
    ]
    ilm_map: dict = {}
    if es_base_url and unique_appnames:
        ds_auth   = es_sess.auth
        ds_verify = es_sess.verify
        with ThreadPoolExecutor(max_workers=10) as pool:
            futs = {
                pool.submit(
                    _fetch_ilm_tier_stateless,
                    es_base_url, dict(es_headers), ds_auth, ds_verify, name,
                ): name
                for name in unique_appnames
            }
            for fut in as_completed(futs, timeout=45):
                name = futs[fut]
                try:
                    ilm_map[name] = fut.result()
                except Exception:
                    ilm_map[name] = "none"

    # ── 5. Assemble per-app catalog items ─────────────────────────────────────
    catalog = []
    for apmid, doc in seen.items():
        appname      = (doc.get("appname")          or "").strip()
        region       = (doc.get("region")           or "").strip()
        request_id   = (doc.get("request_id")       or "").strip()
        status       = (doc.get("status")           or "pending")
        lan_id       = (doc.get("lan_id")           or "").strip()
        timestamp    = (doc.get("@timestamp")       or "").strip()
        entitlements = doc.get("entitlement_groups") or []

        # Resolve worker group from region
        matched_wg = ""
        for ws_name, ws_cfg in workspaces.items():
            ws_regions = ws_cfg.get("regions", [ws_cfg.get("region", "")])
            if region in ws_regions:
                wgs = ws_cfg.get("worker_groups", [])
                if wgs:
                    matched_wg = wgs[0]
                    break
        if not matched_wg:
            for ws_cfg in workspaces.values():
                wgs = ws_cfg.get("worker_groups", [])
                if wgs:
                    matched_wg = wgs[0]
                    break

        # Aggregate Cribl route counts
        total_routes      = 0
        dest              = ""
        logstash_pipeline = None
        for routes_obj in cribl_routes_cache.values():
            cnt, rdest, rpipe = _count_routes_for_app(routes_obj, apmid, appname)
            total_routes += cnt
            if rdest and not dest:
                dest = rdest
            if rpipe and not logstash_pipeline:
                logstash_pipeline = rpipe

        # Count ELK role mappings for this app
        apmid_lower   = apmid.lower()
        appname_lower = appname.lower()
        role_count = sum(
            1
            for mname, mdata in all_role_mappings.items()
            if (
                apmid_lower in mname.lower()
                or appname_lower in mname.lower()
                or any(
                    apmid_lower in r.lower() or appname_lower in r.lower()
                    for r in mdata.get("roles", [])
                )
            )
        )

        catalog.append({
            "apm":              apmid,
            "name":             appname,
            "req_id":           request_id,
            "region":           region,
            "worker_group":     matched_wg,
            "routes":           total_routes,
            "dest":             dest,
            "ilm_tier":         ilm_map.get(appname, "none"),
            "logstash_pipeline": logstash_pipeline,
            "kibana_dashboard": None,
            "roles":            role_count,
            "entitlements":     entitlements,
            "status":           status,
            "submitted_by":     lan_id,
            "date":             timestamp,
        })

    log.info("catalog — assembled %d items", len(catalog))
    return catalog


@app.route("/cribl/catalog")
@app.route("/cribl/catalog/")
@login_required
def catalog_page():
    return render_template("catalog.html")


@app.route("/cribl/api/catalog")
@login_required
def api_catalog():
    now = time.time()
    if (
        _catalog_cache["data"] is not None
        and (now - _catalog_cache["ts"]) < _CATALOG_CACHE_TTL
    ):
        log.info("api/catalog — cache hit (age=%.0fs)", now - _catalog_cache["ts"])
        return jsonify(_catalog_cache["data"])

    try:
        config = load_config()
    except Exception as exc:
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    try:
        data = _build_catalog(config)
        _catalog_cache["data"] = data
        _catalog_cache["ts"]   = time.time()
        return jsonify(data)
    except Exception as exc:
        log.error("api/catalog — build failed:\n%s", traceback.format_exc())
        return jsonify({"errors": [f"Catalog build failed: {exc}"]}), 500


@app.route("/cribl/api/catalog/<apm_id>", methods=["DELETE"])
@admin_required
def api_catalog_delete(apm_id):
    """
    Offboard an app:
      1. Remove matching Cribl routes across all worker groups
      2. Remove matching ELK role_mappings + roles
      3. Update onboarding request status to 'offboarded'

    Query params:
      dry_run=true   — inspect only, make no changes (default: false)
    """
    dry_run = request.args.get("dry_run", "false").lower() in ("true", "1", "yes")
    log.info("api/catalog DELETE — apm_id=%s  dry_run=%s  user=%s",
             apm_id, dry_run, session.get("username"))

    try:
        config = load_config()
    except Exception as exc:
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    results     = {"apm_id": apm_id, "dry_run": dry_run, "actions": []}
    apmid_lower = apm_id.lower()

    def _remove_app_routes(route_list: list) -> int:
        before = len(route_list)
        route_list[:] = [
            r for r in route_list
            if not (
                isinstance(r, dict) and (
                    apmid_lower in str(r.get("id",   "")).lower()
                    or apmid_lower in str(r.get("name", "")).lower()
                )
            )
        ]
        return before - len(route_list)

    workspaces   = {k: v for k, v in config.get("workspaces", {}).items() if not k.startswith("_")}
    creds_global = config.get("credentials", {})

    # ── 1. Remove Cribl routes ────────────────────────────────────────────────
    for ws_name, ws_cfg in workspaces.items():
        worker_groups = ws_cfg.get("worker_groups", [])
        routes_table  = ws_cfg.get("routes_table", "default")
        root_url      = ws_cfg.get("base_url", config.get("base_url", "")).rstrip("/")
        if not root_url:
            continue

        skip_ssl = ws_cfg.get("skip_ssl", config.get("skip_ssl", False))
        ws_token = (ws_cfg.get("token", "") or creds_global.get("token", "")).strip()
        ws_user  = (ws_cfg.get("username", "") or creds_global.get("username", "")).strip()
        ws_pass  = (ws_cfg.get("password", "") or creds_global.get("password", "")).strip()

        ws_sess = http_client.Session()
        ws_sess.verify = not skip_ssl
        if skip_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        cribl_token = ws_token
        if not cribl_token and ws_user:
            try:
                auth_r = ws_sess.post(
                    f"{root_url}/api/v1/auth/login",
                    json={"username": ws_user, "password": ws_pass},
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    timeout=30,
                )
                if auth_r.status_code == 200:
                    cribl_token = auth_r.json().get("token", "")
                else:
                    results["actions"].append({
                        "type": "cribl_auth", "ws": ws_name,
                        "error": f"Auth failed: {auth_r.status_code}",
                    })
                    continue
            except Exception as exc:
                results["actions"].append({"type": "cribl_auth", "ws": ws_name, "error": str(exc)})
                continue

        if cribl_token:
            ws_sess.headers.update({"Authorization": f"Bearer {cribl_token}"})

        for wg in worker_groups:
            routes_url = f"{root_url}/api/v1/m/{wg}/routes/{routes_table}"
            try:
                get_r = ws_sess.get(routes_url, timeout=30)
                get_r.raise_for_status()
                routes_obj = get_r.json()
            except Exception as exc:
                results["actions"].append({
                    "type": "cribl_routes_fetch", "ws": ws_name, "wg": wg, "error": str(exc),
                })
                continue

            root = _unwrap_cribl_response(routes_obj)
            removed = 0
            if isinstance(root.get("routes"), list):
                removed += _remove_app_routes(root["routes"])
            if isinstance(root.get("groups"), list):
                for grp in root["groups"]:
                    if isinstance(grp, dict) and isinstance(grp.get("routes"), list):
                        removed += _remove_app_routes(grp["routes"])

            action_entry = {
                "type": "cribl_routes", "ws": ws_name, "wg": wg,
                "removed": removed, "dry_run": dry_run,
            }
            if removed > 0 and not dry_run:
                try:
                    patch_r = ws_sess.patch(routes_url, json=root, timeout=30)
                    action_entry["status"] = patch_r.status_code
                    if patch_r.status_code >= 400:
                        action_entry["error"] = patch_r.text[:300]
                except Exception as exc:
                    action_entry["error"] = str(exc)
            results["actions"].append(action_entry)

    # ── 2. Remove ELK role_mappings + roles ───────────────────────────────────
    ent_cfg  = config.get("entitlement", {})
    clusters = ent_cfg.get("clusters", [])

    for cluster in clusters:
        cluster_url   = cluster.get("url", "").rstrip("/")
        cluster_token = cluster.get("token", "").strip()
        cluster_auth  = None if cluster_token else HTTPBasicAuth(
            cluster.get("username", ""), cluster.get("password", "")
        )
        cluster_hdrs  = {"Content-Type": "application/json"}
        if cluster_token:
            cluster_hdrs["Authorization"] = f"ApiKey {cluster_token}"

        try:
            all_mappings = fetch_role_mappings(cluster)
        except Exception as exc:
            results["actions"].append({
                "type": "elk_role_mappings_fetch",
                "cluster": cluster.get("name"), "error": str(exc),
            })
            continue

        for mapping_name, mapping in all_mappings.items():
            mn_lower     = mapping_name.lower()
            roles        = mapping.get("roles", [])
            roles_lower  = [r.lower() for r in roles]

            if not (apmid_lower in mn_lower or any(apmid_lower in r for r in roles_lower)):
                continue

            mapping_action = {
                "type": "elk_role_mapping", "cluster": cluster.get("name"),
                "mapping": mapping_name, "dry_run": dry_run,
            }
            if not dry_run:
                try:
                    del_r = http_client.delete(
                        f"{cluster_url}/_security/role_mapping/{mapping_name}",
                        auth=cluster_auth, headers=cluster_hdrs, verify=False, timeout=30,
                    )
                    mapping_action["status"] = del_r.status_code
                except Exception as exc:
                    mapping_action["error"] = str(exc)
            results["actions"].append(mapping_action)

            for role_name in roles:
                if apmid_lower not in role_name.lower():
                    continue
                role_action = {
                    "type": "elk_role", "cluster": cluster.get("name"),
                    "role": role_name, "dry_run": dry_run,
                }
                if not dry_run:
                    try:
                        role_del = http_client.delete(
                            f"{cluster_url}/_security/role/{role_name}",
                            auth=cluster_auth, headers=cluster_hdrs, verify=False, timeout=30,
                        )
                        role_action["status"] = role_del.status_code
                    except Exception as exc:
                        role_action["error"] = str(exc)
                results["actions"].append(role_action)

    # ── 3. Mark as offboarded in Elasticsearch ────────────────────────────────
    es_action = {"type": "es_status_update", "dry_run": dry_run}
    if not dry_run:
        ds            = config.get("datastream", {})
        offboard_idx  = ds.get("index", "cribl-onboarding-requests")
        off_sess, off_base, off_hdrs = _make_es_session_for_catalog(ds)
        if off_base:
            payload = {
                "query":  {"term": {"apmid.keyword": apm_id}},
                "script": {"source": "ctx._source.status = 'offboarded'", "lang": "painless"},
            }
            try:
                upd_r = off_sess.post(
                    f"{off_base}/{offboard_idx}/_update_by_query",
                    json=payload, headers=off_hdrs, timeout=30,
                )
                es_action["status"]  = upd_r.status_code
                es_action["updated"] = (
                    upd_r.json().get("updated", 0) if upd_r.status_code == 200 else 0
                )
            except Exception as exc:
                es_action["error"] = str(exc)
    results["actions"].append(es_action)

    # Bust catalog cache so next /api/catalog call reflects the offboard
    if not dry_run:
        _catalog_cache["data"] = None
        _catalog_cache["ts"]   = 0.0

    return jsonify(results)


@app.route("/cribl/run", methods=["POST"])
@admin_required
def cribl_run():
    """
    Lightweight re-onboard trigger from the Service Catalog.
    Accepts JSON: {apmid, appname, region, workspace?, worker_group?, dry_run?}
    """
    data     = request.get_json(silent=True) or {}
    apmid    = (data.get("apmid")    or "").strip()
    appname  = (data.get("appname")  or "").strip()
    region   = (data.get("region")   or "").strip()
    dry_run  = bool(data.get("dry_run", False))

    if not apmid or not appname:
        return jsonify({"errors": ["apmid and appname are required"]}), 400

    try:
        config = load_config()
    except Exception as exc:
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    workspaces = {k: v for k, v in config.get("workspaces", {}).items() if not k.startswith("_")}

    # Resolve workspace
    workspace_name = (data.get("workspace") or "").strip()
    if not workspace_name:
        for ws_name, ws_cfg in workspaces.items():
            ws_regions = ws_cfg.get("regions", [ws_cfg.get("region", "")])
            if region in ws_regions:
                workspace_name = ws_name
                break
        if not workspace_name and workspaces:
            workspace_name = next(iter(workspaces))

    ws_cfg        = workspaces.get(workspace_name, {})
    worker_group  = (data.get("worker_group") or "").strip()
    worker_groups = [worker_group] if worker_group else ws_cfg.get("worker_groups", [])

    if not worker_groups:
        return jsonify({"errors": [f"No worker groups found for workspace '{workspace_name}'"]}), 400

    apps_payload = [{"apmid": apmid, "app_name": appname}]
    all_output   = ""
    last_rc      = 0

    if CRIBL_SERVICE_URL:
        try:
            route_tmpl   = read_json(get_route_template_path(config, ws_cfg, region))
            dest_tmpl    = read_json(get_dest_template_path(config, ws_cfg, region))
            dest_prefix  = get_dest_prefix(config, ws_cfg, region)
            routes_table = ws_cfg.get("routes_table", "default")
            fallback_pipe = route_tmpl.get("pipeline") or "main"
        except Exception as exc:
            return jsonify({"errors": [f"Config error: {exc}"]}), 500

        for wg in worker_groups:
            payload = {
                "apps": apps_payload,
                "route_template": route_tmpl, "dest_template": dest_tmpl,
                "dest_prefix": dest_prefix, "routes_table": routes_table,
                "dry_run": dry_run, "fallback_pipeline": fallback_pipe,
            }
            try:
                body, status = _svc_post(
                    CRIBL_SERVICE_URL, f"/api/v1/m/{wg}/provision", json=payload
                )
            except Exception as exc:
                body, status = {"error": str(exc)}, 500
            rc = 0 if status < 400 else 1
            if rc:
                last_rc = rc
            all_output += f"\n{'='*60}\n wg={wg}\n{'='*60}\n{json.dumps(body, indent=2)}"
    else:
        for wg in worker_groups:
            form_dict = {
                "appid": apmid, "appname": appname,
                "workspace": workspace_name, "worker_group": wg,
                "region": region, "mode": "single", "log_level": "INFO",
            }
            if dry_run:
                form_dict["dry_run"] = "true"
            cmd, masked = build_pusher_cmd(form_dict, "")
            output, rc  = run_subprocess(cmd, masked)
            all_output += f"\n{'='*60}\n wg={wg}\n{'='*60}\n{output}"
            if rc:
                last_rc = rc

    if last_rc == 0 and not dry_run:
        _catalog_cache["data"] = None
        _catalog_cache["ts"]   = 0.0

    return jsonify({"output": all_output.strip(), "returncode": last_rc})


if __name__ == "__main__":
    log.info("Starting Cribl Framework on 0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
