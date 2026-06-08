#!/usr/bin/env python3
"""
Find appIds actively falling to the default Azure Blob destination in
Cribl Stream — i.e., appIds with no dedicated route/destination.

Uses Cribl's transient live-capture endpoint filtered to only events
heading to the default output, then diffs against the configured
azure_blob destinations.

READ-ONLY: only GET /system/outputs + transient POST /system/capture.
No config is created, modified, or persisted.

Auth (checked in order):
  CRIBL_CLIENT_ID + CRIBL_CLIENT_SECRET   Cribl Cloud OAuth2
  CRIBL_USERNAME  + CRIBL_PASSWORD         Self-managed leader
  CRIBL_TOKEN                              Pre-existing bearer token

CRIBL_URL is always required.

Requirements: Python 3.9+, ``pip install requests``
"""

from __future__ import annotations

import argparse
import csv
import glob as globmod
import io
import json
import logging
import os
import stat
import sys
import textwrap
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("find_default_appids")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CRIBL_CLOUD_LOGIN_URL = "https://login.cribl.cloud/oauth/token"
CRIBL_CLOUD_AUDIENCE = "https://api.cribl.cloud"
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
CAPTURE_READ_TIMEOUT_PAD = 30

# Exit codes
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PARTIAL = 2
EXIT_INTERRUPTED = 130

# ---------------------------------------------------------------------------
# .env file loader (no external dependency)
# ---------------------------------------------------------------------------


def load_env_file(path: str) -> None:
    """Load KEY=VALUE pairs from a file into os.environ.

    Supports:
      - Blank lines and # comments
      - Optional quotes around values (single or double)
      - export KEY=VALUE syntax
    Does NOT override variables already set in the environment.
    """
    try:
        # Warn if the file is world-readable (Unix only)
        if hasattr(os, "stat"):
            try:
                mode = os.stat(path).st_mode
                if mode & stat.S_IROTH:
                    log.warning(
                        "env file %s is world-readable (mode %o). "
                        "Run: chmod 600 %s",
                        path, stat.S_IMODE(mode), path,
                    )
            except OSError:
                pass

        with open(path, encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, 1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    log.warning("%s:%d: skipping line without '='", path, lineno)
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Strip matching quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
                    log.debug("Loaded %s from %s", key, path)
    except FileNotFoundError:
        sys.exit(f"ERROR: env file not found: {path}")
    except PermissionError:
        sys.exit(f"ERROR: cannot read env file (permission denied): {path}")


# ---------------------------------------------------------------------------
# Config file loader
# ---------------------------------------------------------------------------

# Maps capture-section config keys to argparse dest names (where they differ)
_CAPTURE_KEY_MAP = {
    "groups": "group",
    "appid_field": "appid_field",
    "max_events": "max_events",
}


def load_config(path: str) -> dict[str, Any]:
    """Load and validate a JSON config file.

    Returns a flat dict of argparse-compatible defaults.
    Credentials (auth section) are loaded into env vars.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"ERROR: config file not found: {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: invalid JSON in {path}: {exc}")

    if not isinstance(raw, dict):
        sys.exit(f"ERROR: config file must be a JSON object, got {type(raw).__name__}")

    defaults: dict[str, Any] = {}

    # --- auth section -> env vars (won't override existing) ---
    auth = raw.get("auth", {})
    env_mapping = {
        "cribl_url": "CRIBL_URL",
        "client_id": "CRIBL_CLIENT_ID",
        "client_secret": "CRIBL_CLIENT_SECRET",
        "username": "CRIBL_USERNAME",
        "password": "CRIBL_PASSWORD",
        "token": "CRIBL_TOKEN",
    }
    for key, env_name in env_mapping.items():
        val = auth.get(key, "")
        if val and env_name not in os.environ:
            os.environ[env_name] = str(val)
            log.debug("Loaded %s from config auth section", env_name)

    # --- capture section ---
    capture = raw.get("capture", {})
    for key in ("groups", "filter", "seconds", "max_events", "level",
                "rounds", "interval", "appid_field"):
        if key in capture:
            dest = _CAPTURE_KEY_MAP.get(key, key)
            defaults[dest] = capture[key]

    # --- matching section ---
    matching = raw.get("matching", {})
    if "mode" in matching:
        defaults["match_mode"] = matching["mode"]
    if "default_output" in matching:
        defaults["default_output"] = matching["default_output"]
        if "CRIBL_DEFAULT_OUTPUT_ID" not in os.environ:
            os.environ["CRIBL_DEFAULT_OUTPUT_ID"] = str(matching["default_output"])

    # --- output section ---
    output = raw.get("output", {})
    for key in ("format", "append"):
        if key in output:
            defaults[key] = output[key]
    if "diff_csv" in output:
        defaults["diff_csv"] = output["diff_csv"]
    if "lookup" in output:
        defaults["lookup"] = output["lookup"]

    # --- elasticsearch section ---
    es_cfg = raw.get("elasticsearch", {})
    if "url" in es_cfg:
        defaults["es_url"] = es_cfg["url"]
    if "index" in es_cfg:
        defaults["es_index"] = es_cfg["index"]
    # Auth via env vars — push into environment if present in config
    es_env_map = {
        "api_key": "ES_API_KEY",
        "username": "ES_USERNAME",
        "password": "ES_PASSWORD",
    }
    for key, env_name in es_env_map.items():
        val = es_cfg.get(key, "")
        if val and env_name not in os.environ:
            os.environ[env_name] = str(val)

    # --- logging section ---
    log_cfg = raw.get("logging", {})
    if "log_file" in log_cfg:
        defaults["log_file"] = log_cfg["log_file"]
    if "verbose" in log_cfg:
        defaults["verbose"] = log_cfg["verbose"]

    # --- connection section ---
    conn = raw.get("connection", {})
    if "verify_ssl" in conn:
        defaults["no_verify_ssl"] = not conn["verify_ssl"]
    if "env_file" in conn:
        defaults["env_file"] = conn["env_file"]

    return defaults


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------


def _build_session(verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    session.verify = verify_ssl
    if not verify_ssl:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class CriblAPIError(Exception):
    def __init__(self, response: requests.Response) -> None:
        self.status_code = response.status_code
        self.url = response.url
        try:
            body = response.json()
            self.detail = (
                body.get("message") or body.get("error") or json.dumps(body)
            )
        except (ValueError, KeyError):
            self.detail = response.text[:500] if response.text else "(empty body)"
        super().__init__(
            f"HTTP {self.status_code} from {self.url}: {self.detail}"
        )


class AuthenticationError(Exception):
    """Raised when no valid credentials are available."""


def _raise_for_status(resp: requests.Response) -> None:
    if resp.status_code >= 400:
        raise CriblAPIError(resp)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class CriblAuth:
    def __init__(self, cribl_url: str, session: requests.Session) -> None:
        self._cribl_url = cribl_url.rstrip("/")
        self._session = session
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at:
                return self._token
            self._authenticate()
            if not self._token:
                raise AuthenticationError("Authentication succeeded but no token was returned.")
            return self._token

    def ensure_fresh(self) -> None:
        """Force a token refresh if close to expiry (within 120s)."""
        with self._lock:
            if not self._token or time.time() > self._expires_at - 120:
                log.info("Token expiring soon or missing — refreshing")
                self._authenticate()

    def _authenticate(self) -> None:
        client_id = os.environ.get("CRIBL_CLIENT_ID", "").strip()
        client_secret = os.environ.get("CRIBL_CLIENT_SECRET", "").strip()
        username = os.environ.get("CRIBL_USERNAME", "").strip()
        password = os.environ.get("CRIBL_PASSWORD", "").strip()
        static_token = os.environ.get("CRIBL_TOKEN", "").strip()

        if client_id and client_secret:
            self._cloud_oauth(client_id, client_secret)
        elif username and password:
            self._leader_login(username, password)
        elif static_token:
            self._token = static_token
            self._expires_at = time.time() + 86400
            log.info("Using static bearer token from CRIBL_TOKEN")
        else:
            raise AuthenticationError(
                "No Cribl credentials found. Set one of:\n"
                "  CRIBL_CLIENT_ID + CRIBL_CLIENT_SECRET  (Cribl Cloud)\n"
                "  CRIBL_USERNAME  + CRIBL_PASSWORD        (self-managed leader)\n"
                "  CRIBL_TOKEN                             (pre-existing bearer token)"
            )

    def _cloud_oauth(self, client_id: str, client_secret: str) -> None:
        log.info(
            "Authenticating via Cribl Cloud OAuth2 (%s)", CRIBL_CLOUD_LOGIN_URL
        )
        resp = self._session.post(
            CRIBL_CLOUD_LOGIN_URL,
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "audience": CRIBL_CLOUD_AUDIENCE,
            },
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        _raise_for_status(resp)
        data = resp.json()
        self._token = data["access_token"]
        self._expires_at = time.time() + data.get("expires_in", 3600) - 60
        log.info(
            "Cloud OAuth token acquired (expires in %ds)",
            data.get("expires_in", 0),
        )

    def _leader_login(self, username: str, password: str) -> None:
        url = f"{self._cribl_url}/api/v1/auth/login"
        log.info("Authenticating via leader login (%s)", url)
        resp = self._session.post(
            url,
            json={"username": username, "password": password},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        _raise_for_status(resp)
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            body_preview = resp.text[:300] if resp.text else "(empty)"
            raise AuthenticationError(
                f"Login endpoint returned non-JSON response.\n"
                f"  URL: {url}\n"
                f"  Status: {resp.status_code}\n"
                f"  Body: {body_preview}\n"
                f"  Check that CRIBL_URL is correct (should be the base URL, "
                f"e.g. https://leader:9000 or https://main-org.cribl.cloud)"
            )
        self._token = data.get("token") or data.get("access_token")
        if not self._token:
            raise AuthenticationError(
                f"Login succeeded (HTTP {resp.status_code}) but no token in response.\n"
                f"  Keys returned: {list(data.keys())}"
            )
        self._expires_at = time.time() + 3600
        log.info("Leader login token acquired")


# ---------------------------------------------------------------------------
# Cribl API client  (read-only)
# ---------------------------------------------------------------------------


class CriblClient:
    def __init__(
        self,
        cribl_url: str,
        group: str,
        auth: CriblAuth,
        session: requests.Session,
    ) -> None:
        self._base = f"{cribl_url.rstrip('/')}/api/v1/m/{group}"
        self._group = group
        self._auth = auth
        self._session = session

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth.token}",
            "Content-Type": "application/json",
            "Accept": "application/x-ndjson, application/json",
        }

    def list_outputs(self) -> list[dict[str, Any]]:
        url = f"{self._base}/system/outputs"
        log.debug("GET %s", url)
        resp = self._session.get(
            url, headers=self._headers(), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
        )
        _raise_for_status(resp)
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            body_preview = resp.text[:200] if resp.text else "(empty)"
            raise RuntimeError(
                f"Group '{self._group}': expected JSON from {url} "
                f"but got: {body_preview}\n"
                f"Check that the group name is correct and exists in Cribl."
            )
        return data.get("items", data) if isinstance(data, dict) else data

    def list_azure_blob_outputs(self) -> list[dict[str, Any]]:
        return [o for o in self.list_outputs() if o.get("type") == "azure_blob"]

    def list_routes(self) -> list[dict[str, Any]]:
        """GET /routes — list all configured routes."""
        url = f"{self._base}/routes"
        log.debug("GET %s", url)
        resp = self._session.get(
            url, headers=self._headers(), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
        )
        _raise_for_status(resp)
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            body_preview = resp.text[:200] if resp.text else "(empty)"
            raise RuntimeError(
                f"Group '{self._group}': expected JSON from {url} "
                f"but got: {body_preview}\n"
                f"Check that the group name is correct and exists in Cribl."
            )
        # Routes live under data.items or just items depending on version
        if isinstance(data, dict):
            routes = data.get("items") or data.get("routes") or []
            # Some versions nest routes under a single group object
            if not routes and "groups" in data:
                for g in data["groups"].values():
                    routes.extend(g.get("routes", []))
            return routes
        return data

    def find_default_output_id(self) -> str | None:
        """Find the output ID marked as the default destination."""
        for o in self.list_outputs():
            if o.get("type") == "default":
                return o.get("defaultId")
        return None

    def capture_live(
        self,
        *,
        filter_expr: str = "true",
        duration: int = 30,
        max_events: int = 5000,
        level: int = 3,
    ) -> list[dict[str, Any]]:
        """POST /system/capture — transient, writes nothing to config."""
        url = f"{self._base}/system/capture"
        body = {
            "filter": filter_expr,
            "duration": duration,
            "maxEvents": max_events,
            "level": level,
            "workerThreshold": 0,
        }
        log.info(
            "POST %s  (level=%d, duration=%ds, maxEvents=%d, filter=%r)",
            url, level, duration, max_events, filter_expr,
        )
        resp = self._session.post(
            url,
            headers=self._headers(),
            json=body,
            stream=True,
            timeout=(CONNECT_TIMEOUT, duration + CAPTURE_READ_TIMEOUT_PAD),
        )
        _raise_for_status(resp)

        events: list[dict[str, Any]] = []
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                log.debug("Skipping non-JSON line from capture stream")
        return events


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------


def get_nested(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def extract_appid(event: dict[str, Any], field_path: str) -> str | None:
    """Extract appId — tries top-level fields, then parses _raw as JSON."""
    val = get_nested(event, field_path)
    if val is not None:
        return str(val)

    raw = event.get("_raw")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                val = get_nested(parsed, field_path)
                if val is not None:
                    return str(val)
        except (json.JSONDecodeError, TypeError):
            pass
    return None


# ---------------------------------------------------------------------------
# Capture with progress
# ---------------------------------------------------------------------------


def _progress_capture(
    client: CriblClient,
    *,
    filter_expr: str,
    duration: int,
    max_events: int,
    level: int,
) -> list[dict[str, Any]]:
    print(
        f"  Capturing for up to {duration}s (level={level}, "
        f"max={max_events}) ...",
        end="",
        flush=True,
    )
    t0 = time.monotonic()
    events = client.capture_live(
        filter_expr=filter_expr,
        duration=duration,
        max_events=max_events,
        level=level,
    )
    elapsed = time.monotonic() - t0
    print(f" done ({elapsed:.1f}s, {len(events)} events)")
    return events


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def match_appid_to_dest(
    app_id: str,
    destinations: list[dict[str, Any]],
    match_mode: str,
) -> str | None:
    """Return the first destination ``id`` whose containerName matches.

    exact      containerName == appId  (case-insensitive)
    contains   appId must appear in containerName  (one-directional)
    partition  containerName exact OR appId in partitionExpr
    """
    app_lower = app_id.lower()
    for dest in destinations:
        container = (dest.get("containerName") or "").lower()
        part_expr = dest.get("partitionExpr") or ""
        dest_id = dest.get("id", "?")

        if match_mode == "exact":
            if container == app_lower:
                return dest_id
        elif match_mode == "contains":
            if app_lower in container:
                return dest_id
        elif match_mode == "partition":
            if container == app_lower or app_id in part_expr:
                return dest_id
    return None


def check_lookup_route_dest_status(
    lookup_appids_hitting_default: set[str],
    destinations: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    match_mode: str,
) -> list[dict[str, Any]]:
    """Check whether lookup appIds (have containers) also have routes and
    destinations configured in Cribl.

    Returns a list of dicts with status for each lookup appId that was
    captured hitting the default destination:
      {apmId, has_destination, destination_id, has_route, route_id, route_output, status}
    """
    results = []
    for app_id in sorted(lookup_appids_hitting_default):
        # Check if a matching azure_blob destination exists.
        # First try the standard containerName-based match, then fall back
        # to checking if the destination id or name contains the appId
        # (e.g. destination "azure_blob:prod-my-app" for appId "my-app").
        app_lower = app_id.lower()
        dest_id = match_appid_to_dest(app_id, destinations, match_mode)
        if dest_id is None:
            for dest in destinations:
                did = (dest.get("id") or "").lower()
                dname = (dest.get("name") or "").lower()
                if app_lower in did or app_lower in dname:
                    dest_id = dest.get("id", "?")
                    break

        # Check if any route references this appId — route names often
        # contain the appId as a substring (e.g. route "prod-my-app-blob"
        # for appId "my-app").
        route_match_id = None
        route_match_output = None
        for route in routes:
            route_filter = str(route.get("filter", ""))
            route_name = str(route.get("name", ""))
            route_output = route.get("output", "")
            route_id = route.get("id", route_name)

            # Check if the route name or ID contains this appId
            if app_lower in route_name.lower() or app_lower in str(route_id).lower():
                route_match_id = route_id
                route_match_output = route_output
                break

            # Check if the route filter references this appId
            if app_lower in route_filter.lower():
                route_match_id = route_id
                route_match_output = route_output
                break

            # Check if the route output points to a destination that
            # matches this appId's container
            if route_output and dest_id and route_output == dest_id:
                route_match_id = route_id
                route_match_output = route_output
                break

        has_dest = dest_id is not None
        has_route = route_match_id is not None

        if has_dest and has_route:
            status = "CONFIGURED (has route + destination)"
        elif has_dest and not has_route:
            status = "MISSING ROUTE (destination exists, no route)"
        elif not has_dest and has_route:
            status = "MISSING DESTINATION (route exists, no destination)"
        else:
            status = "MISSING BOTH (no route, no destination)"

        results.append({
            "apmId": app_id,
            "has_destination": has_dest,
            "destination_id": dest_id or "NONE",
            "has_route": has_route,
            "route_id": route_match_id or "NONE",
            "route_output": route_match_output or "NONE",
            "status": status,
        })

    return results


def _print_lookup_status_table(results: list[dict[str, Any]]) -> None:
    """Print a table showing route/destination status for lookup appIds."""
    if not results:
        return

    apm_w = max(len(r["apmId"]) for r in results)
    apm_w = max(apm_w, 5)
    dest_w = max(len(r["destination_id"]) for r in results)
    dest_w = max(dest_w, 14)
    route_w = max(len(r["route_id"]) for r in results)
    route_w = max(route_w, 8)

    print(
        f"\n{'apmId':<{apm_w}s}   {'Has Dest':>8s}   {'Destination':<{dest_w}s}   "
        f"{'Has Route':>9s}   {'Route':<{route_w}s}   Status"
    )
    print("-" * (apm_w + dest_w + route_w + 55))

    for r in results:
        dest_flag = "YES" if r["has_destination"] else "NO"
        route_flag = "YES" if r["has_route"] else "NO"
        marker = ""
        if not r["has_destination"] or not r["has_route"]:
            marker = " <<<"
        print(
            f"{r['apmId']:<{apm_w}s}   {dest_flag:>8s}   {r['destination_id']:<{dest_w}s}   "
            f"{route_flag:>9s}   {r['route_id']:<{route_w}s}   {r['status']}{marker}"
        )

    missing_route = sum(1 for r in results if not r["has_route"])
    missing_dest = sum(1 for r in results if not r["has_destination"])
    missing_both = sum(1 for r in results if not r["has_route"] and not r["has_destination"])
    fully_configured = sum(1 for r in results if r["has_route"] and r["has_destination"])

    print(f"\n  Total lookup appIds hitting default : {len(results)}")
    print(f"  Fully configured (route + dest)     : {fully_configured}")
    print(f"  Missing route only                  : {missing_route - missing_both}")
    print(f"  Missing destination only            : {missing_dest - missing_both}")
    print(f"  Missing both                        : {missing_both}")


def write_lookup_status_csv(
    results: list[dict[str, Any]],
    output_path: str,
) -> None:
    """Write lookup appId route/destination status to CSV."""
    if not results:
        return
    header = ["apmId", "has_destination", "destination_id", "has_route",
              "route_id", "route_output", "status"]
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nLookup status CSV written to {output_path}")


# ---------------------------------------------------------------------------
# Elasticsearch indexing
# ---------------------------------------------------------------------------


class ElasticsearchClient:
    """Minimal Elasticsearch client — indexes unmatched appId documents."""

    def __init__(
        self,
        url: str,
        index: str,
        *,
        api_key: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self._url = url.strip().rstrip("/")
        self._index = index.strip().strip("/")
        self._session = _build_session(verify_ssl=verify_ssl)
        self._session.headers.update({"Content-Type": "application/x-ndjson"})
        self._auth: tuple[str, str] | None = None

        if api_key:
            self._session.headers["Authorization"] = f"ApiKey {api_key}"
        elif username and password:
            self._auth = (username, password)

    def test_connection(self) -> tuple[bool, str]:
        """Quick connectivity check — GET / on the ES cluster."""
        try:
            resp = self._session.get(
                self._url,
                auth=self._auth,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            if resp.status_code == 401:
                return False, f"Authentication failed (HTTP 401): {resp.text[:300]}"
            if resp.status_code == 403:
                return False, f"Authorization denied (HTTP 403): {resp.text[:300]}"
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
            try:
                data = resp.json()
                version = data.get("version", {}).get("number", "unknown")
                name = data.get("name", "unknown")
                return True, f"Connected — cluster '{name}', ES {version}"
            except (json.JSONDecodeError, ValueError):
                return False, (
                    f"ES URL returned non-JSON (HTTP {resp.status_code}). "
                    f"Body: {resp.text[:300]}\n"
                    f"Check that {self._url} is the ES API, not Kibana or a load balancer."
                )
        except requests.RequestException as exc:
            return False, f"Connection failed: {exc}"

    def index_results(
        self,
        rows: list[tuple[str, str, str, str, int]],
        *,
        group: str,
        total_events: int,
        is_new: bool = True,
    ) -> int:
        """Bulk-index appId results into Elasticsearch. Returns docs indexed."""
        if not rows:
            return 0

        if not self._index:
            log.error("ES index name is empty — cannot index")
            return 0

        bulk_url = f"{self._url}/{self._index}/_bulk"
        timestamp = datetime.now(timezone.utc).isoformat()
        # Sanitize group for safe embedding in JSON
        safe_group = group.replace("\n", " ").replace("\r", " ")

        ndjson_lines: list[str] = []
        for apm_id, app_name, output_id, matched, count in rows:
            action = json.dumps({"index": {}})
            doc = json.dumps({
                "@timestamp": timestamp,
                "group": safe_group,
                "apmId": apm_id,
                "appName": app_name,
                "outputId": output_id,
                "matched_destination": matched,
                "is_unmatched": matched == "DEFAULT",
                "is_new": is_new,
                "event_count": count,
                "total_events_captured": total_events,
            })
            ndjson_lines.append(action)
            ndjson_lines.append(doc)

        body = "\n".join(ndjson_lines) + "\n"

        log.info("ES _bulk request: %d docs, %d bytes to %s", len(rows), len(body), bulk_url)

        try:
            resp = self._session.post(
                bulk_url,
                auth=self._auth,
                data=body.encode("utf-8"),
                timeout=(CONNECT_TIMEOUT, 60),
            )
        except requests.ConnectionError as exc:
            log.error("ES connection refused: %s\n  Check that ES_URL=%s is reachable.", exc, self._url)
            return 0
        except requests.Timeout:
            log.error("ES request timed out after 60s to %s", bulk_url)
            return 0
        except requests.RequestException as exc:
            log.error("ES request failed: %s", exc)
            return 0

        # Log full response details for debugging
        log.debug("ES response: HTTP %d, %d bytes, url=%s", resp.status_code, len(resp.text), resp.url)

        # Check if we got redirected (url mismatch = probably wrong endpoint)
        if resp.url and resp.url.rstrip("/") != bulk_url.rstrip("/"):
            log.warning(
                "ES request was redirected: %s -> %s. "
                "Check that ES_URL points to ES, not Kibana or a proxy.",
                bulk_url, resp.url,
            )

        if resp.status_code == 401:
            log.error(
                "ES authentication failed (HTTP 401).\n"
                "  URL: %s\n  Response: %s\n"
                "  Fix: set ES_API_KEY or ES_USERNAME+ES_PASSWORD in config/env.",
                bulk_url, resp.text[:500],
            )
            return 0
        if resp.status_code == 403:
            log.error(
                "ES authorization denied (HTTP 403).\n"
                "  URL: %s\n  Response: %s\n"
                "  Fix: ensure the ES user/key has write access to index '%s'.",
                bulk_url, resp.text[:500], self._index,
            )
            return 0
        if resp.status_code >= 400:
            log.error(
                "ES bulk index failed: HTTP %d\n  URL: %s\n  Response: %s",
                resp.status_code, bulk_url, resp.text[:2000],
            )
            return 0

        try:
            result = resp.json()
        except (json.JSONDecodeError, ValueError):
            log.error(
                "ES returned non-JSON (HTTP %d).\n  URL: %s\n  Body: %s\n"
                "  This usually means ES_URL points to a non-ES service (Kibana, proxy, HTML page).",
                resp.status_code, bulk_url, resp.text[:1000],
            )
            return 0

        if result.get("errors"):
            error_count = 0
            seen_errors: set[str] = set()
            for item in result.get("items", []):
                err = item.get("index", {}).get("error")
                if err:
                    error_count += 1
                    err_key = f"{err.get('type', '?')}:{err.get('reason', '?')[:100]}"
                    if err_key not in seen_errors:
                        seen_errors.add(err_key)
                        log.error(
                            "ES doc error [%d of %d]:\n"
                            "  type: %s\n"
                            "  reason: %s\n"
                            "  caused_by: %s\n"
                            "  full_error: %s",
                            error_count, len(rows),
                            err.get("type", "?"),
                            err.get("reason", "?"),
                            json.dumps(err.get("caused_by", {})),
                            json.dumps(err)[:2000],
                        )
            if len(seen_errors) < error_count:
                log.error(
                    "  ... and %d more docs with same error(s)",
                    error_count - len(seen_errors),
                )
            log.warning(
                "ES bulk index: %d/%d docs FAILED. Unique error types: %s",
                error_count, len(rows), ", ".join(sorted(seen_errors)),
            )
            print(
                f"\nElasticsearch: {error_count}/{len(rows)} docs FAILED. "
                f"Check log file for details."
            )
            indexed = len(rows) - error_count
            if indexed > 0:
                print(f"  ({indexed} doc(s) indexed successfully)")
            return indexed

        indexed = len(result.get("items", []))
        log.info("Elasticsearch: indexed %d doc(s) to %s", indexed, self._index)
        print(f"\nElasticsearch: indexed {indexed} doc(s) to {self._index}")
        return indexed


def _build_es_client(args: argparse.Namespace) -> ElasticsearchClient | None:
    """Build an ElasticsearchClient from args/env, or return None if not configured."""
    es_url = (args.es_url or os.environ.get("ES_URL", "")).strip()
    es_index = (args.es_index or os.environ.get("ES_INDEX", "")).strip()

    if not es_url or not es_index:
        return None

    api_key = os.environ.get("ES_API_KEY", "").strip()
    username = os.environ.get("ES_USERNAME", "").strip()
    password = os.environ.get("ES_PASSWORD", "").strip()

    return ElasticsearchClient(
        url=es_url,
        index=es_index,
        api_key=api_key or None,
        username=username or None,
        password=password or None,
        verify_ssl=not args.no_verify_ssl,
    )


# ---------------------------------------------------------------------------
# Lookup table loader
# ---------------------------------------------------------------------------


def load_lookup_appids(path: str) -> set[str]:
    """Load known container names from a lookup JSON file.

    Reads the ``azure_storage_account_containers`` key and returns the
    values as a set of strings (case-insensitive, lowered).

    Example file::

        {
          "azure_storage_account_containers": ["app-one", "app-two"],
          "other_key": "ignored"
        }
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        log.error("Lookup file not found: %s", path)
        return set()
    except json.JSONDecodeError as exc:
        log.error("Lookup file is not valid JSON: %s — %s", path, exc)
        return set()

    containers = data.get("azure_storage_account_containers", [])
    if not isinstance(containers, list):
        log.error(
            "Lookup file %s: 'azure_storage_account_containers' must be a list, got %s",
            path, type(containers).__name__,
        )
        return set()

    ids = {str(c).lower() for c in containers if c}
    log.info("Loaded %d container(s) from lookup %s", len(ids), path)
    return ids


# ---------------------------------------------------------------------------
# Diff against previous CSV
# ---------------------------------------------------------------------------


def load_previous_unmatched(csv_path: str) -> set[str]:
    """Load unmatched apmIds from a previous CSV run."""
    ids: set[str] = set()
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row.get("matched_destination") == "DEFAULT":
                    ids.add(row.get("apmId", ""))
    except FileNotFoundError:
        pass
    ids.discard("")
    return ids


def load_all_known_appids(csv_path: str) -> set[str]:
    """Load all apmIds already present in a CSV (any match status)."""
    ids: set[str] = set()
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                aid = row.get("apmId", "").strip()
                if aid:
                    ids.add(aid)
    except FileNotFoundError:
        pass
    return ids


def find_latest_csv(output_dir: str, current_output: str) -> str | None:
    """Find the most recent appids_without_destination_*.csv other than current."""
    pattern = os.path.join(output_dir, "appids_without_destination_*.csv")
    candidates = sorted(globmod.glob(pattern), reverse=True)
    current_abs = os.path.abspath(current_output)
    for path in candidates:
        if os.path.abspath(path) != current_abs:
            return path
    return None


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def write_csv(
    rows: list[tuple[str, str, str, str, int]],
    output_path: str,
    append: bool,
) -> None:
    """Write results to CSV."""
    csv_header = ["apmId", "appName", "outputId", "matched_destination", "event_count"]
    file_exists = append and os.path.isfile(output_path)

    if append and file_exists:
        existing: set[tuple[str, str, str]] = set()
        with open(output_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                existing.add((
                    row.get("apmId", ""),
                    row.get("appName", ""),
                    row.get("outputId", ""),
                ))
        new_rows = [r for r in rows if (r[0], r[1], r[2]) not in existing]
        with open(output_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            for apm_id, app_name, output_id, matched, count in new_rows:
                writer.writerow([apm_id, app_name, output_id, matched, count])
        print(
            f"\nAppended {len(new_rows)} new row(s) to {output_path}"
            f" ({len(rows) - len(new_rows)} already present)"
        )
    else:
        write_mode = "a" if append else "w"
        with open(output_path, write_mode, newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            if write_mode == "w" or not file_exists:
                writer.writerow(csv_header)
            for apm_id, app_name, output_id, matched, count in rows:
                writer.writerow([apm_id, app_name, output_id, matched, count])
        print(f"\nCSV results written to {output_path}")


def write_json(
    rows: list[tuple[str, str, str, str, int]],
    output_path: str,
    group: str,
    total_events: int,
) -> None:
    """Write results to JSON."""
    records = []
    for apm_id, app_name, output_id, matched, count in rows:
        records.append({
            "apmId": apm_id,
            "appName": app_name,
            "outputId": output_id,
            "matched_destination": matched,
            "event_count": count,
        })

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "group": group,
        "total_events": total_events,
        "total_appids": len({r[0] for r in rows}),
        "unmatched_count": len({r[0] for r in rows if r[3] == "DEFAULT"}),
        "results": records,
    }

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2)
    print(f"\nJSON results written to {output_path}")


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def run_inspect(client: CriblClient, appid_field: str, level: int) -> None:
    print("=" * 70)
    print(f"INSPECTION MODE  (group: {client._group})")
    print("=" * 70)

    default_id = _resolve_default_output_id(client)

    # --- default output ID ---
    print("\n[1/3] Detecting default output ID\n")
    if default_id:
        print(f"  Default output ID: {default_id!r}")
    else:
        print("  Could not auto-detect default output ID.")

    # --- destinations ---
    print("\n[2/3] GET /system/outputs  (type==azure_blob)\n")
    try:
        destinations = client.list_azure_blob_outputs()
    except CriblAPIError as exc:
        print(f"  ERROR: {exc}")
        destinations = []

    if destinations:
        print(f"Found {len(destinations)} azure_blob destination(s).\n")
        print("First one (full JSON):")
        print(json.dumps(destinations[0], indent=2))
        print(f"\n  containerName = {destinations[0].get('containerName')!r}")
        print(f"  partitionExpr = {destinations[0].get('partitionExpr')!r}")
        print(f"\nAll destination IDs and containers:")
        for d in destinations:
            print(f"  {d.get('id', '?'):30s} -> {d.get('containerName', '')!r}")
    else:
        print("  *** No azure_blob destinations found. ***")

    # --- capture ---
    print(f"\n[3/3] POST /system/capture  (10s, max 20 events, level={level})\n")
    try:
        events = _progress_capture(
            client, filter_expr="true", duration=10, max_events=20, level=level,
        )
    except CriblAPIError as exc:
        print(f"  ERROR: {exc}")
        events = []

    if not events:
        print("  *** No events captured. Check source activity. ***")
        print("=" * 70)
        return

    first = events[0]
    print(f"\nFirst event (truncated):")
    print(json.dumps(first, indent=2)[:3000])

    # Scan routing fields across ALL captured events
    print("\n--- Routing fields across all captured events ---")
    routing_fields = ["cribl_output", "__outputId", "output", "cribl_route"]
    for field in routing_fields:
        values: set[str] = set()
        for ev in events:
            v = ev.get(field)
            if v is not None:
                values.add(str(v))
        if values:
            print(f"  {field}: {values}")

    # Show appId
    val = extract_appid(first, appid_field)
    source = "top-level"
    if get_nested(first, appid_field) is None and val is not None:
        source = "parsed from _raw"
    print(f"\n  {appid_field!r} in first event = {val!r}  ({source})")

    if val is None:
        print(f"\n  WARNING: {appid_field!r} not found.")
        print(f"  Top-level keys: {sorted(first.keys())}")
        raw = first.get("_raw")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    print(f"  Keys inside _raw: {sorted(parsed.keys())}")
            except (json.JSONDecodeError, TypeError):
                print("  _raw is present but not valid JSON.")

    # Suggest filter
    print("\n--- Suggested next steps ---")
    if default_id:
        print(f"Detected default output ID: {default_id!r}")
        print(f"Suggested filter to capture only default-bound events:")
        print(f'  --filter "__outputId===\'{default_id}\'"')
    else:
        print("Could not auto-detect default output. Use the routing field")
        print("values above to build your --filter. Examples:")
        print('  --filter "__outputId===\'<output_id>\'"')
        print('  --filter "cribl_output===\'<output_id>\'"')
    print("\nThen run without --inspect for the full analysis.")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Default output ID resolution
# ---------------------------------------------------------------------------


def _resolve_default_output_id(client: CriblClient) -> str | None:
    """Resolve the default output ID from env var or Cribl config."""
    env_val = os.environ.get("CRIBL_DEFAULT_OUTPUT_ID", "").strip()
    if env_val:
        return env_val
    return client.find_default_output_id()


# ---------------------------------------------------------------------------
# Thread-safe print lock (for multi-group parallel output)
# ---------------------------------------------------------------------------

_print_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Per-group analysis (used by both single and multi-group modes)
# ---------------------------------------------------------------------------


def _analyse_group(
    client: CriblClient,
    args: argparse.Namespace,
    effective_filter: str,
    buffered: bool = False,
) -> tuple[
    list[tuple[str, str, str, str, int]],
    set[str],
    set[str],
    int,
    int,
    int,
    str,
]:
    """Run capture rounds for one group, return (rows, app_ids, unmatched_ids,
    total_events, raw_parse_count, missing_count, output_text).

    When buffered=True, print output is captured and returned as the last
    tuple element instead of going to stdout (avoids interleaving in
    multi-group parallel mode).
    """
    buf = io.StringIO() if buffered else None
    _print = (lambda *a, **kw: print(*a, file=buf, **kw)) if buf else print

    rounds = args.rounds
    interval = args.interval

    combo_counts: Counter[tuple[str, str, str]] = Counter()
    raw_parse_count = 0
    missing_count = 0
    total_events = 0
    interrupted = False

    # Graceful Ctrl+C saves partial results
    try:
        for rnd in range(1, rounds + 1):
            if rounds > 1:
                _print(f"\n  --- Round {rnd}/{rounds} ({client._group}) ---")

            # Refresh token before each round
            client._auth.ensure_fresh()

            _print(
                f"  Capturing for up to {args.seconds}s (level={args.level}, "
                f"max={args.max_events}) ...",
                end="", flush=True,
            )
            t0 = time.monotonic()
            events = client.capture_live(
                filter_expr=effective_filter,
                duration=args.seconds,
                max_events=args.max_events,
                level=args.level,
            )
            elapsed = time.monotonic() - t0
            _print(f" done ({elapsed:.1f}s, {len(events)} events)")
            log.info(
                "Round %d/%d [%s]: captured %d events in %.1fs",
                rnd, rounds, client._group, len(events), elapsed,
            )
            total_events += len(events)

            for ev in events:
                top_val = get_nested(ev, args.appid_field)
                apm_id = extract_appid(ev, args.appid_field)
                if apm_id is not None:
                    app_name = extract_appid(ev, "appName") or ""
                    output_id = str(ev.get("__outputId", ""))
                    combo_counts[(apm_id, app_name, output_id)] += 1
                    if top_val is None:
                        raw_parse_count += 1
                else:
                    missing_count += 1

            if rounds > 1:
                app_ids_so_far = {apm_id for apm_id, _, _ in combo_counts}
                _print(
                    f"  Cumulative: {len(app_ids_so_far)} distinct "
                    f"{args.appid_field} values, {total_events} events"
                )

            if rnd < rounds:
                _print(f"  Waiting {interval}s before next round ...", end="", flush=True)
                time.sleep(interval)
                _print(" done")

    except KeyboardInterrupt:
        interrupted = True
        _print(f"\n  Interrupted after {total_events} events — saving partial results ...")

    app_ids = {apm_id for apm_id, _, _ in combo_counts}

    if total_events == 0:
        output_text = buf.getvalue() if buf else ""
        return [], set(), set(), 0, raw_parse_count, missing_count, output_text

    # Fetch destinations and match
    destinations = client.list_azure_blob_outputs()
    log.info("[%s] %d azure_blob destinations found", client._group, len(destinations))
    _print(f"  Found {len(destinations)} azure_blob destination(s) ({client._group}).")
    if destinations:
        id_width = max(len(d.get("id", "")) for d in destinations)
        for d in destinations:
            _print(
                f"    {d['id']:<{id_width}s}  "
                f"container={d.get('containerName', '')!r}"
            )

    rows: list[tuple[str, str, str, str, int]] = []
    for (apm_id, app_name, output_id), count in sorted(combo_counts.items()):
        dest = match_appid_to_dest(apm_id, destinations, args.match_mode)
        matched = dest if dest else "DEFAULT"
        rows.append((apm_id, app_name, output_id, matched, count))

    unmatched_ids = {r[0] for r in rows if r[3] == "DEFAULT"}
    log.info(
        "[%s] Done: %d events, %d distinct appIds, %d unmatched, %d from _raw, %d missing field",
        client._group, total_events, len(app_ids), len(unmatched_ids),
        raw_parse_count, missing_count,
    )
    output_text = buf.getvalue() if buf else ""

    if interrupted:
        raise KeyboardInterrupt

    return rows, app_ids, unmatched_ids, total_events, raw_parse_count, missing_count, output_text


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def run_dry_run(
    clients: list[CriblClient],
    args: argparse.Namespace,
) -> int:
    """Validate connectivity, auth, and config without capturing events."""
    print("=" * 70)
    print("DRY RUN — validating configuration (no events will be captured)")
    print("=" * 70)

    groups = [c._group for c in clients]
    ok = True

    # 1. Auth check
    print("\n[1/4] Authentication")
    try:
        _ = clients[0]._auth.token
        print("  OK — token acquired")
    except (AuthenticationError, CriblAPIError) as exc:
        print(f"  FAIL — {exc}")
        ok = False

    # 2. Default output ID
    print("\n[2/4] Default output ID")
    default_id = args.default_output or _resolve_default_output_id(clients[0])
    if default_id:
        print(f"  OK — {default_id!r}")
    else:
        print("  WARN — could not resolve. Will capture ALL events.")

    # 3. Per-group connectivity
    print(f"\n[3/4] Group connectivity ({len(groups)} group(s))")
    for client in clients:
        try:
            dests = client.list_azure_blob_outputs()
            print(f"  {client._group}: OK — {len(dests)} azure_blob destination(s)")
        except (CriblAPIError, requests.ConnectionError) as exc:
            print(f"  {client._group}: FAIL — {exc}")
            ok = False

    # 4. Elasticsearch (if configured)
    print("\n[4/4] Elasticsearch")
    es_client = _build_es_client(args)
    if es_client:
        es_ok, es_msg = es_client.test_connection()
        if es_ok:
            print(f"  OK — {es_client._url} / {es_client._index}")
            print(f"  {es_msg}")
        else:
            print(f"  FAIL — {es_msg}")
            ok = False
    else:
        print("  Not configured (results will not be indexed)")

    # Summary
    effective_filter = args.filter
    if effective_filter == "true" and default_id:
        effective_filter = f"__outputId==='{default_id}'"

    total_time = args.rounds * args.seconds + max(0, args.rounds - 1) * args.interval
    print(f"\n--- Run plan ---")
    print(f"  Groups        : {', '.join(groups)}")
    print(f"  Filter        : {effective_filter}")
    print(f"  Rounds        : {args.rounds} x {args.seconds}s (interval: {args.interval}s)")
    print(f"  Est. duration : {total_time // 60}m {total_time % 60}s")
    print(f"  Max events    : {args.max_events} per round per group")
    print(f"  Output        : {args.output} ({args.format})")
    print(f"  Log file      : {args.log_file or '(stdout only)'}")
    print("=" * 70)

    if ok:
        print("\nAll checks passed. Remove --dry-run to execute.")
        return EXIT_OK
    else:
        print("\nSome checks FAILED. Fix the issues above before running.")
        return EXIT_ERROR


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------


def run_analysis(
    clients: list[CriblClient],
    args: argparse.Namespace,
) -> int:
    """Run analysis across all groups. Returns exit code."""
    groups = [c._group for c in clients]
    log.info(
        "Starting analysis: groups=%s, rounds=%d, seconds=%d, interval=%d",
        groups, args.rounds, args.seconds, args.interval,
    )
    # Resolve default output ID
    default_id = args.default_output or _resolve_default_output_id(clients[0])

    # Auto-build filter if user left it at default
    effective_filter = args.filter
    if effective_filter == "true":
        if default_id:
            effective_filter = f"__outputId==='{default_id}'"
            print(f"Auto-detected default output: {default_id!r}")
            print(f"Using filter: {effective_filter}")
        else:
            print(
                "WARNING: Could not auto-detect default output ID.\n"
                "Capturing ALL events. Run with --inspect to find the right\n"
                "filter, or pass --filter explicitly."
            )

    all_rows: list[tuple[str, str, str, str, int]] = []
    all_app_ids: set[str] = set()
    all_unmatched: set[str] = set()
    grand_total_events = 0
    grand_raw_parse = 0
    grand_missing = 0
    failed_groups: list[str] = []

    groups = [c._group for c in clients]
    print(f"\n[Step 1/3] Live capture ({args.rounds} round(s), "
          f"{args.seconds}s each, groups: {', '.join(groups)})")

    # Parallel capture across multiple groups
    if len(clients) > 1:
        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            futures = {
                pool.submit(_analyse_group, client, args, effective_filter, True): client._group
                for client in clients
            }
            for future in as_completed(futures):
                group_name = futures[future]
                try:
                    rows, app_ids, unmatched, total_ev, raw_ct, miss_ct, output_text = future.result()
                    # Print buffered output atomically
                    with _print_lock:
                        if output_text:
                            print(output_text, end="")
                    all_rows.extend(rows)
                    all_app_ids |= app_ids
                    all_unmatched |= unmatched
                    grand_total_events += total_ev
                    grand_raw_parse += raw_ct
                    grand_missing += miss_ct
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    log.error("Group %r failed: %s", group_name, exc)
                    failed_groups.append(group_name)
    else:
        try:
            rows, app_ids, unmatched, total_ev, raw_ct, miss_ct, _ = _analyse_group(
                clients[0], args, effective_filter,
            )
            all_rows = rows
            all_app_ids = app_ids
            all_unmatched = unmatched
            grand_total_events = total_ev
            grand_raw_parse = raw_ct
            grand_missing = miss_ct
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log.error("Group %r failed: %s", clients[0]._group, exc)
            failed_groups.append(clients[0]._group)

    if failed_groups:
        log.warning("Groups failed: %s", ", ".join(failed_groups))
        print(f"\nWARNING: {len(failed_groups)} group(s) failed: {', '.join(failed_groups)}")

    log.info(
        "Capture complete: %d total events, %d distinct appIds, %d groups OK, %d groups failed",
        grand_total_events, len(all_app_ids), len(groups) - len(failed_groups), len(failed_groups),
    )

    if grand_total_events == 0:
        log.warning("No events captured across all groups")
        print(
            "\nNo events captured. Possible reasons:\n"
            "  - No traffic hitting the default output right now\n"
            "  - Filter expression doesn't match (run --inspect to check)\n"
            "  - Duration too short (try --seconds 60)"
        )
        return EXIT_PARTIAL if failed_groups else EXIT_ERROR

    if not all_app_ids:
        log.warning("No %s values found in %d events", args.appid_field, grand_total_events)
        print(
            f"\nNo {args.appid_field} values found in {grand_total_events} events."
            f"\nRun with --inspect to examine event structure."
        )
        return EXIT_PARTIAL if failed_groups else EXIT_ERROR

    # Load known apmIds from existing CSV to filter to new-only
    known_appids: set[str] = set()
    existing_csv = args.diff_csv or find_latest_csv(".", args.output)
    if existing_csv:
        known_appids = load_all_known_appids(existing_csv)

    # Load lookup table — appIds with existing containers are excluded
    lookup_appids: set[str] = set()
    if args.lookup:
        lookup_appids = load_lookup_appids(args.lookup)

    excluded = known_appids | {aid for aid in all_app_ids if aid.lower() in lookup_appids}
    new_app_ids = all_app_ids - excluded
    new_rows = [r for r in all_rows if r[0] in new_app_ids] if excluded else all_rows
    new_unmatched = {r[0] for r in new_rows if r[3] == "DEFAULT"}
    in_lookup = {aid for aid in all_app_ids if aid.lower() in lookup_appids}

    # --- Check route/destination status for lookup appIds hitting default ---
    # These appIds have containers (in lookup) but are still hitting the
    # default destination — check if they have routes and destinations
    # configured in Cribl.
    # When the capture filter targets the default output (auto or manual),
    # every captured appId is heading to default. But if the user supplied
    # a custom filter, we verify via the outputId in captured rows.
    if default_id:
        lookup_hitting_default = {
            aid for aid in all_app_ids
            if aid.lower() in lookup_appids
            and any(r[0] == aid and default_id in r[2] for r in all_rows)
        }
    else:
        # No default output known — all lookup appIds in captured data are suspect
        lookup_hitting_default = {
            aid for aid in all_app_ids
            if aid.lower() in lookup_appids
        }
    lookup_status_results: list[dict[str, Any]] = []
    if lookup_hitting_default:
        log.info(
            "%d lookup appId(s) are hitting the default destination — "
            "checking route/destination config",
            len(lookup_hitting_default),
        )
        # Fetch destinations and routes from the first successful client
        try:
            all_destinations = clients[0].list_azure_blob_outputs()
            all_routes = clients[0].list_routes()
            log.info(
                "Fetched %d destinations, %d routes for lookup status check",
                len(all_destinations), len(all_routes),
            )
            lookup_status_results = check_lookup_route_dest_status(
                lookup_hitting_default,
                all_destinations,
                all_routes,
                args.match_mode,
            )
        except Exception as exc:
            log.warning("Could not check route/destination status: %s", exc)
            print(f"\n  WARNING: Could not check route/destination status: {exc}")

    log.info(
        "appId summary: total=%d, in_csv=%d, in_lookup=%d, "
        "lookup_hitting_default=%d, new=%d, new_unmatched=%d",
        len(all_app_ids), len(all_app_ids & known_appids), len(in_lookup),
        len(lookup_hitting_default), len(new_app_ids), len(new_unmatched),
    )
    print(f"\n  Distinct {args.appid_field} values (total) : {len(all_app_ids)}")
    print(f"  Already known (in previous CSV)          : {len(all_app_ids & known_appids)}")
    if lookup_appids:
        print(f"  In lookup (have container)               : {len(in_lookup)}")
        if lookup_hitting_default:
            print(f"  In lookup BUT hitting default            : {len(lookup_hitting_default)}  !!!")
    print(f"  New {args.appid_field} values              : {len(new_app_ids)}")

    # Show lookup status report if any lookup appIds are hitting default
    if lookup_status_results:
        print(f"\n[ALERT] Lookup appIds with containers hitting default destination")
        print(f"These appIds have Azure containers (per lookup table) but are still")
        print(f"falling through to the default output — checking route/destination config:")
        _print_lookup_status_table(lookup_status_results)

        # Write lookup status CSV
        lookup_csv_path = args.output.rsplit(".", 1)[0] + "_lookup_status.csv" \
            if args.output.endswith(".csv") \
            else f"lookup_status_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        write_lookup_status_csv(lookup_status_results, lookup_csv_path)

    if excluded and not new_app_ids:
        log.info("No new appIds found — all %d excluded (csv=%d, lookup=%d)",
                 len(all_app_ids), len(all_app_ids & known_appids), len(in_lookup))
        print(f"\nNo new {args.appid_field} values found — all {len(all_app_ids)} already accounted for")
        if lookup_status_results:
            print(f"  BUT {len(lookup_hitting_default)} lookup appId(s) need route/destination attention (see above)")
        return EXIT_OK

    # Print table (new apmIds only)
    print(f"\n[Step 3/3] Matching new apmIds (mode={args.match_mode!r})")
    _print_table(new_rows, new_app_ids, new_unmatched, grand_total_events)

    # Diff against previous CSV (unmatched-specific)
    new_unmatched_diff: set[str] | None = None
    if existing_csv:
        prev_unmatched = load_previous_unmatched(existing_csv)
        all_unmatched_current = {r[0] for r in all_rows if r[3] == "DEFAULT"}
        new_unmatched_diff = all_unmatched_current - prev_unmatched
        removed_ids = prev_unmatched - all_unmatched_current
        print(f"\n--- Diff vs {existing_csv} ---")
        print(f"  Previously unmatched : {len(prev_unmatched)}")
        print(f"  Currently unmatched  : {len(all_unmatched_current)}")
        print(f"  Newly appeared       : {len(new_unmatched_diff)}")
        if new_unmatched_diff:
            for aid in sorted(new_unmatched_diff):
                print(f"    + {aid}")
        print(f"  No longer seen       : {len(removed_ids)}")
        if removed_ids:
            for aid in sorted(removed_ids):
                print(f"    - {aid}")

    # Write output — only new rows
    log.info("Writing %d new row(s) to output", len(new_rows))
    fmt = args.format
    if fmt == "csv":
        write_csv(new_rows, args.output, args.append)
    elif fmt == "json":
        json_path = args.output.rsplit(".", 1)[0] + ".json" if args.output.endswith(".csv") else args.output
        write_json(new_rows, json_path, ", ".join(groups), grand_total_events)
    elif fmt == "both":
        write_csv(new_rows, args.output, args.append)
        json_path = args.output.rsplit(".", 1)[0] + ".json"
        write_json(new_rows, json_path, ", ".join(groups), grand_total_events)

    # Index results to Elasticsearch
    es_client = _build_es_client(args)
    if es_client and new_rows:
        log.info("Indexing %d doc(s) to Elasticsearch %s/%s", len(new_rows), es_client._url, es_client._index)
        es_client.index_results(
            new_rows,
            group=", ".join(groups),
            total_events=grand_total_events,
            is_new=bool(known_appids),
        )

    exit_code = EXIT_PARTIAL if failed_groups else EXIT_OK
    log.info("Analysis complete — exit code %d", exit_code)
    return exit_code


def _print_table(
    rows: list[tuple[str, str, str, str, int]],
    app_ids: set[str],
    unmatched_ids: set[str],
    total_events: int,
) -> None:
    apm_w = max((len(r[0]) for r in rows), default=5)
    app_w = max((len(r[1]) for r in rows), default=7)
    out_w = max((len(r[2]) for r in rows), default=8)
    apm_w = max(apm_w, 5)
    app_w = max(app_w, 7)
    out_w = max(out_w, 8)
    print(
        f"\n{'apmId':<{apm_w}s}   {'appName':<{app_w}s}   "
        f"{'outputId':<{out_w}s}   {'Events':>6s}   Matched Destination"
    )
    print("-" * (apm_w + app_w + out_w + 45))
    for apm_id, app_name, output_id, matched, count in rows:
        marker = " <<<" if matched == "DEFAULT" else ""
        print(
            f"{apm_id:<{apm_w}s}   {app_name:<{app_w}s}   "
            f"{output_id:<{out_w}s}   {count:>6d}   {matched}{marker}"
        )

    print(f"\nTotal distinct apmIds : {len(app_ids)}")
    print(f"Unmatched (DEFAULT)   : {len(unmatched_ids)}")
    print(f"Total events captured : {total_events}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Find appIds with no matching Azure Blob destination (read-only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            How it works:
              1. Auto-detects the default output ID from Cribl config
              2. Captures live events heading to that default output
                 (transient — writes nothing to Cribl config)
              3. Extracts distinct appId values from captured events
              4. GETs azure_blob destinations and diffs

            Auth priority (first match wins):
              1. Environment variables (CRIBL_CLIENT_ID, etc.)
              2. --config JSON auth section
              3. --env-file .env file

            Optional env vars:
              CRIBL_DEFAULT_OUTPUT_ID   Override default output auto-detection
              ES_URL                    Elasticsearch URL
              ES_INDEX                  Elasticsearch index name
              ES_API_KEY                Elasticsearch API key auth
              ES_USERNAME / ES_PASSWORD Elasticsearch basic auth

            Capture levels (--level):
              0   Before pre-processing pipeline
              1   Before routes
              2   Before post-processing pipeline
              3   Before destination (default — final routed state)

            Match modes (--match-mode):
              exact      containerName == appId  (case-insensitive)
              contains   appId must appear in containerName  (one-directional)
              partition  containerName exact OR appId in partitionExpr

            Workflow:
              # 1. Inspect — discover field names and verify connectivity
              python find_default_appids.py --config config.json --inspect

              # 2. Dry run — validate config without capturing events
              python find_default_appids.py --config config.json --dry-run

              # 3. Full analysis
              python find_default_appids.py --config config.json

              # 4. Override config values from CLI
              python find_default_appids.py --config config.json --rounds 20

              # 5. Without config file (all CLI args)
              python find_default_appids.py --group mygroup --seconds 60

            Exit codes:
              0   Success
              1   Fatal error (auth, connectivity, no events)
              2   Partial failure (some groups failed, results from others saved)
              130 Interrupted (Ctrl+C — partial results saved)
        """),
    )

    parser.add_argument(
        "--config", default=None, metavar="PATH",
        help="Path to JSON config file (see config.example.json). "
             "CLI args override config values.",
    )
    parser.add_argument(
        "--group", nargs="+", default=None,
        help="Cribl worker group name(s) — multiple groups run in parallel",
    )
    parser.add_argument(
        "--filter", default=None,
        help="JavaScript filter for capture (default: auto-detect from default output)",
    )
    parser.add_argument(
        "--default-output", default=None, metavar="ID",
        help="Default output ID (e.g. 'azure_blob:foo-default'). "
             "Overrides auto-detection and CRIBL_DEFAULT_OUTPUT_ID env var.",
    )
    parser.add_argument("--seconds", type=int, default=None,
                        help="Capture duration in seconds (default: 30)")
    parser.add_argument("--max-events", type=int, default=None,
                        help="Max events to capture (default: 5000, API max: 10000)")
    parser.add_argument("--level", type=int, default=None, choices=[0, 1, 2, 3],
                        help="Capture stage (default: 3 = before destination)")
    parser.add_argument("--appid-field", default=None,
                        help="Dot-separated field path for appId (default: 'apmId')")
    parser.add_argument("--match-mode", default=None,
                        choices=["exact", "contains", "partition"],
                        help="How to match appId to destination container (default: exact)")
    parser.add_argument("--output", default=None,
                        help="Output CSV path (default: appids_without_destination_YYYYMMDD_HHMMSS.csv)")
    parser.add_argument("--append", action="store_true", default=None,
                        help="Append to CSV instead of overwriting (deduplicates automatically)")
    parser.add_argument("--rounds", type=int, default=None,
                        help="Number of capture rounds (default: 1)")
    parser.add_argument("--interval", type=int, default=None,
                        help="Seconds to wait between capture rounds (default: 60)")
    parser.add_argument("--format", default=None, choices=["csv", "json", "both"],
                        help="Output format (default: csv)")
    parser.add_argument(
        "--diff-csv", default=None, metavar="PATH",
        help="Compare against a previous CSV to show newly appeared appIds. "
             "If omitted, auto-detects the latest CSV in the current directory.",
    )
    parser.add_argument(
        "--lookup", default=None, metavar="PATH",
        help="Path to lookup JSON file (e.g. APP_foo.json). "
             "appIds found in 'azure_storage_account_containers' are excluded from results.",
    )
    parser.add_argument(
        "--es-url", default=None, metavar="URL",
        help="Elasticsearch URL (e.g. https://elk:9200). Can also be set via ES_URL env var.",
    )
    parser.add_argument(
        "--es-index", default=None, metavar="NAME",
        help="Elasticsearch index name for results. Can also be set via ES_INDEX env var.",
    )
    parser.add_argument(
        "--inspect", action="store_true",
        help="Inspection mode: show destinations, capture sample, suggest filter",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate auth, connectivity, and config without capturing events",
    )
    parser.add_argument(
        "--env-file", default=None, metavar="PATH",
        help="Path to .env file with credentials (CRIBL_URL, CRIBL_CLIENT_ID, "
             "CRIBL_CLIENT_SECRET, etc.). Does not override existing env vars.",
    )
    parser.add_argument(
        "--log-file", default=None, metavar="PATH",
        help="Write log output to file (in addition to stderr)",
    )
    parser.add_argument(
        "--no-verify-ssl", action="store_true", default=None,
        help="Disable SSL certificate verification",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", default=None,
        help="Enable debug logging",
    )
    return parser


def main() -> None:
    # Pass 1: peek at --config to load defaults before full parse
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", default=None)
    pre_args, _ = pre_parser.parse_known_args()

    config_defaults: dict[str, Any] = {}
    if pre_args.config:
        config_defaults = load_config(pre_args.config)

    # Pass 2: full parse
    parser = _build_parser()
    args = parser.parse_args()

    # Merge: CLI args > config.json > hardcoded defaults
    for key, value in config_defaults.items():
        if getattr(args, key, None) is None:
            setattr(args, key, value)

    # Hardcoded fallbacks for anything still None
    _fallbacks = {
        "filter": "true",
        "seconds": 30,
        "max_events": 5000,
        "level": 3,
        "appid_field": "apmId",
        "match_mode": "exact",
        "output": f"appids_without_destination_{time.strftime('%Y%m%d_%H%M%S')}.csv",
        "append": False,
        "rounds": 1,
        "interval": 60,
        "format": "csv",
        "verbose": False,
        "no_verify_ssl": False,
    }
    for key, value in _fallbacks.items():
        if getattr(args, key, None) is None:
            setattr(args, key, value)

    # Validate required fields
    if not args.group:
        parser.error("--group is required (via CLI or config file)")

    # --- Logging setup ---
    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_format = "%(asctime)s  %(levelname)-8s  %(message)s"
    log_datefmt = "%H:%M:%S"

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))

    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=log_datefmt,
        handlers=handlers,
    )

    # Load .env file if specified (before reading any env vars)
    if args.env_file:
        load_env_file(args.env_file)

    # Resolve --default-output from CLI > env var (env resolved later in functions)
    if args.default_output:
        os.environ["CRIBL_DEFAULT_OUTPUT_ID"] = args.default_output

    cribl_url = os.environ.get("CRIBL_URL", "").strip()
    if not cribl_url:
        sys.exit(
            "ERROR: Set CRIBL_URL (via env, --config auth section, or --env-file)"
        )

    session = _build_session(verify_ssl=not args.no_verify_ssl)
    auth = CriblAuth(cribl_url, session)

    # Build one CriblClient per group
    clients = [CriblClient(cribl_url, g, auth, session) for g in args.group]

    try:
        if args.dry_run:
            sys.exit(run_dry_run(clients, args))
        elif args.inspect:
            for client in clients:
                run_inspect(client, args.appid_field, args.level)
            sys.exit(EXIT_OK)
        else:
            sys.exit(run_analysis(clients, args))
    except AuthenticationError as exc:
        log.error("%s", exc)
        sys.exit(EXIT_ERROR)
    except CriblAPIError as exc:
        log.error("Cribl API error: %s", exc)
        sys.exit(EXIT_ERROR)
    except requests.ConnectionError as exc:
        log.error("Connection failed: %s", exc)
        sys.exit(EXIT_ERROR)
    except KeyboardInterrupt:
        print("\nInterrupted — partial results saved if available.")
        sys.exit(EXIT_INTERRUPTED)


if __name__ == "__main__":
    main()
