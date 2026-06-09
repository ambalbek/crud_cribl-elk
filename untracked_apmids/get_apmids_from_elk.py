#!/usr/bin/env python3
"""
Get all unique apmId/appName from ELK, compare with Cribl azure_blob
destinations and routes, and index results back into ELK.

Same matching logic as find_default_appids.py but sources apmIds from
ELK (logs-k8s-container-all*) instead of Cribl live capture.

Flow:
  1. Query logs-k8s-container-all* for all unique apmId + appName
  2. Fetch azure_blob destinations + routes from Cribl API
  3. Match apmIds to destinations/routes (exact/contains/partition)
  4. Index results into ELK (e.g. cribl-untracked-appids)

Usage:
  python get_apmids_from_elk.py --es-url https://es:9200 --cribl-url https://main-org.cribl.cloud --group default

Auth via env vars:
  ES_API_KEY / ES_USERNAME+ES_PASSWORD   — Elasticsearch auth
  CRIBL_CLIENT_ID + CRIBL_CLIENT_SECRET  — Cribl Cloud OAuth2
  CRIBL_USERNAME  + CRIBL_PASSWORD       — Cribl self-managed
  CRIBL_TOKEN                            — Pre-existing bearer token
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import stat
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_INDEX = "logs-k8s-container-all*"
DEFAULT_RESULT_INDEX = "cribl-untracked-appids"
CRIBL_CLOUD_LOGIN_URL = "https://login.cribl.cloud/oauth/token"
CRIBL_CLOUD_AUDIENCE = "https://api.cribl.cloud"
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30


# ---------------------------------------------------------------------------
# .env file loader (same as find_default_appids.py)
# ---------------------------------------------------------------------------

def load_env_file(path: str) -> None:
    """Load KEY=VALUE pairs from a file into os.environ.

    Supports blank lines, # comments, optional quotes, export prefix.
    Does NOT override variables already set in the environment.
    """
    try:
        if hasattr(os, "stat"):
            try:
                mode = os.stat(path).st_mode
                if mode & stat.S_IROTH:
                    print(f"WARNING: env file {path} is world-readable (mode {stat.S_IMODE(mode):o})",
                          file=sys.stderr)
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
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        sys.exit(f"ERROR: env file not found: {path}")
    except PermissionError:
        sys.exit(f"ERROR: cannot read env file (permission denied): {path}")


# ---------------------------------------------------------------------------
# Config file loader (same as find_default_appids.py)
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict[str, Any]:
    """Load and validate a JSON config file.

    Returns a flat dict of argparse-compatible defaults.
    Credentials (auth/elasticsearch sections) are loaded into env vars.
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

    # --- capture section (only group is relevant here) ---
    capture = raw.get("capture", {})
    if "groups" in capture:
        groups = capture["groups"]
        if isinstance(groups, list) and groups:
            defaults["group"] = groups[0]

    # --- matching section ---
    matching = raw.get("matching", {})
    if "mode" in matching:
        defaults["match_mode"] = matching["mode"]

    # --- elasticsearch section ---
    es_cfg = raw.get("elasticsearch", {})
    if "url" in es_cfg:
        defaults["es_url"] = es_cfg["url"]
    if "index" in es_cfg:
        defaults["result_index"] = es_cfg["index"]
    es_env_map = {
        "api_key": "ES_API_KEY",
        "username": "ES_USERNAME",
        "password": "ES_PASSWORD",
    }
    for key, env_name in es_env_map.items():
        val = es_cfg.get(key, "")
        if val and env_name not in os.environ:
            os.environ[env_name] = str(val)

    # --- connection section ---
    conn = raw.get("connection", {})
    if "verify_ssl" in conn:
        defaults["no_verify_ssl"] = not conn["verify_ssl"]
    if "env_file" in conn:
        defaults["env_file"] = conn["env_file"]

    return defaults


# ---------------------------------------------------------------------------
# HTTP sessions
# ---------------------------------------------------------------------------

def build_es_session(verify_ssl: bool = True) -> requests.Session:
    s = requests.Session()
    s.verify = verify_ssl
    s.headers["Content-Type"] = "application/json"
    api_key = os.environ.get("ES_API_KEY", "").strip()
    username = os.environ.get("ES_USERNAME", "").strip()
    password = os.environ.get("ES_PASSWORD", "").strip()
    if api_key:
        s.headers["Authorization"] = f"ApiKey {api_key}"
    elif username and password:
        s.auth = (username, password)
    return s


def build_cribl_session(verify_ssl: bool = True) -> requests.Session:
    s = requests.Session()
    s.verify = verify_ssl
    if not verify_ssl:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET", "POST"], raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


# ---------------------------------------------------------------------------
# Cribl Auth (same as find_default_appids.py)
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
                raise RuntimeError("Authentication succeeded but no token was returned.")
            return self._token

    def _authenticate(self) -> None:
        client_id = os.environ.get("CRIBL_CLIENT_ID", "").strip()
        client_secret = os.environ.get("CRIBL_CLIENT_SECRET", "").strip()
        username = os.environ.get("CRIBL_USERNAME", "").strip()
        password = os.environ.get("CRIBL_PASSWORD", "").strip()
        static_token = os.environ.get("CRIBL_TOKEN", "").strip()

        if client_id and client_secret:
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
            if resp.status_code >= 400:
                sys.exit(f"ERROR: Cribl OAuth failed: HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            self._token = data["access_token"]
            self._expires_at = time.time() + data.get("expires_in", 3600) - 60

        elif username and password:
            url = f"{self._cribl_url}/api/v1/auth/login"
            resp = self._session.post(
                url, json={"username": username, "password": password},
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            if resp.status_code >= 400:
                sys.exit(f"ERROR: Cribl login failed: HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            self._token = data.get("token") or data.get("access_token")
            self._expires_at = time.time() + 3600

        elif static_token:
            self._token = static_token
            self._expires_at = time.time() + 86400

        else:
            sys.exit(
                "ERROR: No Cribl credentials. Set CRIBL_CLIENT_ID+CRIBL_CLIENT_SECRET, "
                "CRIBL_USERNAME+CRIBL_PASSWORD, or CRIBL_TOKEN"
            )


# ---------------------------------------------------------------------------
# Cribl API client (read-only, same as find_default_appids.py)
# ---------------------------------------------------------------------------

class CriblClient:
    def __init__(self, cribl_url: str, group: str, auth: CriblAuth, session: requests.Session) -> None:
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
        resp = self._session.get(url, headers=self._headers(), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        if resp.status_code >= 400:
            sys.exit(f"ERROR: Cribl outputs API: HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        return data.get("items", data) if isinstance(data, dict) else data

    def list_azure_blob_outputs(self) -> list[dict[str, Any]]:
        return [o for o in self.list_outputs() if o.get("type") == "azure_blob"]

    def list_routes(self) -> list[dict[str, Any]]:
        url = f"{self._base}/routes"
        resp = self._session.get(url, headers=self._headers(), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        if resp.status_code >= 400:
            sys.exit(f"ERROR: Cribl routes API: HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        if isinstance(data, dict):
            routes = data.get("items") or data.get("routes") or []
            if not routes and "groups" in data:
                for g in data["groups"].values():
                    routes.extend(g.get("routes", []))
            return routes
        return data


# ---------------------------------------------------------------------------
# Step 1: Fetch apmIds from ELK
# ---------------------------------------------------------------------------

def fetch_all_apmids(session: requests.Session, es_url: str, days: int) -> list[dict]:
    """Composite aggregation to get ALL unique apmId/appName pairs."""
    url = f"{es_url}/{SOURCE_INDEX}/_search"
    results = []
    after = None

    while True:
        sources = [
            {"apmId": {"terms": {"field": "apmId.keyword", "missing_bucket": True}}},
            {"appName": {"terms": {"field": "appName.keyword", "missing_bucket": True}}},
        ]
        composite = {"size": 1000, "sources": sources}
        if after:
            composite["after"] = after

        query = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"@timestamp": {"gte": f"now-{days}d", "lte": "now"}}},
                        {"exists": {"field": "apmId"}},
                    ]
                }
            },
            "aggs": {"unique_pairs": {"composite": composite}},
        }

        resp = session.post(url, json=query, timeout=(CONNECT_TIMEOUT, 60))
        if resp.status_code != 200:
            sys.exit(f"ERROR: ES returned HTTP {resp.status_code}: {resp.text[:500]}")

        data = resp.json()
        buckets = data["aggregations"]["unique_pairs"]["buckets"]
        if not buckets:
            break

        for b in buckets:
            results.append({
                "apmId": b["key"]["apmId"] or "",
                "appName": b["key"]["appName"] or "",
                "_doc_count": b["doc_count"],
            })

        after = buckets[-1]["key"]
        print(f"  fetched {len(results)} unique pairs so far...", file=sys.stderr)

    return results


def dedup_by_apmid(results: list[dict]) -> list[dict]:
    """Keep only the appName with highest doc_count per apmId, then drop counts."""
    best: dict[str, dict] = {}
    for row in results:
        apm_id = row["apmId"]
        if apm_id not in best or row["_doc_count"] > best[apm_id]["_doc_count"]:
            best[apm_id] = row
    return [{"apmId": r["apmId"], "appName": r["appName"]} for r in best.values()]


# ---------------------------------------------------------------------------
# Step 3: Matching (same logic as find_default_appids.py)
# ---------------------------------------------------------------------------

def match_appid_to_dest(
    app_id: str,
    destinations: list[dict[str, Any]],
    match_mode: str,
) -> str | None:
    """Return the first destination id whose containerName matches.

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


def check_route_dest_status(
    apmids: list[dict],
    destinations: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    match_mode: str,
) -> list[dict[str, Any]]:
    """Check whether each apmId has a matching destination and route in Cribl.

    Returns a list of dicts:
      {apmId, appName, has_destination, destination_id, has_route, route_id, route_output, status}
    """
    results = []
    for row in sorted(apmids, key=lambda r: r["apmId"]):
        app_id = row["apmId"]
        app_name = row["appName"]
        app_lower = app_id.lower()

        # Match destination: first by containerName, then fallback to id/name
        dest_id = match_appid_to_dest(app_id, destinations, match_mode)
        if dest_id is None:
            for dest in destinations:
                did = (dest.get("id") or "").lower()
                dname = (dest.get("name") or "").lower()
                if app_lower in did or app_lower in dname:
                    dest_id = dest.get("id", "?")
                    break

        # Match route: check name/id, filter, or output pointing to matched dest
        route_match_id = None
        route_match_output = None
        for route in routes:
            route_filter = str(route.get("filter", ""))
            route_name = str(route.get("name", ""))
            route_output = route.get("output", "")
            route_id = route.get("id", route_name)

            if app_lower in route_name.lower() or app_lower in str(route_id).lower():
                route_match_id = route_id
                route_match_output = route_output
                break

            if app_lower in route_filter.lower():
                route_match_id = route_id
                route_match_output = route_output
                break

            if route_output and dest_id and route_output == dest_id:
                route_match_id = route_id
                route_match_output = route_output
                break

        has_dest = dest_id is not None
        has_route = route_match_id is not None

        if has_dest and has_route:
            status = "CONFIGURED"
        elif has_dest and not has_route:
            status = "MISSING_ROUTE"
        elif not has_dest and has_route:
            status = "MISSING_DESTINATION"
        else:
            status = "MISSING_BOTH"

        results.append({
            "apmId": app_id,
            "appName": app_name,
            "has_destination": has_dest,
            "destination_id": dest_id or "NONE",
            "has_route": has_route,
            "route_id": route_match_id or "NONE",
            "route_output": route_match_output or "NONE",
            "status": status,
        })

    return results


# ---------------------------------------------------------------------------
# Step 4: Index results back into ELK
# ---------------------------------------------------------------------------

def index_to_elk(session: requests.Session, es_url: str, index: str, rows: list[dict], group: str) -> int:
    """Bulk-index results into ELK. Returns count of indexed docs."""
    if not rows:
        return 0

    bulk_url = f"{es_url}/{index}/_bulk"
    timestamp = datetime.now(timezone.utc).isoformat()
    ndjson_lines = []

    for row in rows:
        action = json.dumps({"index": {}})
        doc = json.dumps({
            "@timestamp": timestamp,
            "group": group,
            "apmId": row["apmId"],
            "appName": row["appName"],
            "has_destination": row["has_destination"],
            "destination_id": row["destination_id"],
            "has_route": row["has_route"],
            "route_id": row["route_id"],
            "route_output": row["route_output"],
            "status": row["status"],
            "is_unmatched": row["status"] != "CONFIGURED",
        })
        ndjson_lines.append(action)
        ndjson_lines.append(doc)

    body = "\n".join(ndjson_lines) + "\n"
    resp = session.post(
        bulk_url,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
        timeout=(CONNECT_TIMEOUT, 60),
    )

    if resp.status_code >= 400:
        print(f"ERROR: ES bulk index failed: HTTP {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
        return 0

    result = resp.json()
    if result.get("errors"):
        error_count = sum(1 for item in result.get("items", []) if item.get("index", {}).get("error"))
        print(f"WARNING: {error_count}/{len(rows)} docs failed to index", file=sys.stderr)
        return len(rows) - error_count

    return len(result.get("items", []))


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_status_table(results: list[dict]) -> None:
    """Print a table showing route/destination status for each apmId."""
    if not results:
        return

    apm_w = max(max(len(r["apmId"]) for r in results), 5)
    app_w = max(max(len(r["appName"]) for r in results), 7)
    dest_w = max(max(len(r["destination_id"]) for r in results), 14)
    route_w = max(max(len(r["route_id"]) for r in results), 8)

    header = (
        f"{'apmId':<{apm_w}s}   {'appName':<{app_w}s}   {'Has Dest':>8s}   "
        f"{'Destination':<{dest_w}s}   {'Has Route':>9s}   {'Route':<{route_w}s}   Status"
    )
    print(f"\n{header}")
    print("-" * len(header))

    for r in results:
        dest_flag = "YES" if r["has_destination"] else "NO"
        route_flag = "YES" if r["has_route"] else "NO"
        marker = "" if r["status"] == "CONFIGURED" else " <<<"
        print(
            f"{r['apmId']:<{apm_w}s}   {r['appName']:<{app_w}s}   {dest_flag:>8s}   "
            f"{r['destination_id']:<{dest_w}s}   {route_flag:>9s}   "
            f"{r['route_id']:<{route_w}s}   {r['status']}{marker}"
        )

    configured = sum(1 for r in results if r["status"] == "CONFIGURED")
    missing_route = sum(1 for r in results if r["status"] == "MISSING_ROUTE")
    missing_dest = sum(1 for r in results if r["status"] == "MISSING_DESTINATION")
    missing_both = sum(1 for r in results if r["status"] == "MISSING_BOTH")

    print(f"\n  Total apmIds              : {len(results)}")
    print(f"  Fully configured          : {configured}")
    print(f"  Missing route only        : {missing_route}")
    print(f"  Missing destination only  : {missing_dest}")
    print(f"  Missing both              : {missing_both}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Pass 1: peek at --config to load defaults before full parse
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", default=None)
    pre_args, _ = pre_parser.parse_known_args()

    config_defaults: dict[str, Any] = {}
    if pre_args.config:
        config_defaults = load_config(pre_args.config)

    # Pass 2: full parse
    parser = argparse.ArgumentParser(
        description="Get apmIds from ELK, compare with Cribl destinations/routes, store results")
    parser.add_argument("--config", default=None, help="Path to config.json (same format as find_default_appids.py)")
    parser.add_argument("--env-file", default=None, help="Path to .env file")
    parser.add_argument("--es-url", default=None, help="Elasticsearch URL")
    parser.add_argument("--cribl-url", default=None, help="Cribl API URL")
    parser.add_argument("--group", default=None, help="Cribl worker group (default: default)")
    parser.add_argument("--match-mode", choices=["exact", "contains", "partition"],
                        default=None, help="Matching mode (default: contains)")
    parser.add_argument("--days", type=int, default=30, help="Look back N days (default: 30)")
    parser.add_argument("--result-index", default=None,
                        help=f"ELK index to store results (default: {DEFAULT_RESULT_INDEX})")
    parser.add_argument("--output", "-o", help="Also save CSV to file")
    parser.add_argument("--no-verify-ssl", action="store_true", default=None)
    args = parser.parse_args()

    # Merge: CLI args > config.json > hardcoded defaults
    for key, value in config_defaults.items():
        if getattr(args, key, None) is None:
            setattr(args, key, value)

    # Hardcoded fallbacks for anything still None
    _fallbacks: dict[str, Any] = {
        "group": "default",
        "match_mode": "contains",
        "no_verify_ssl": False,
    }
    for key, value in _fallbacks.items():
        if getattr(args, key, None) is None:
            setattr(args, key, value)

    # Load .env file if specified (before reading env vars)
    if args.env_file:
        load_env_file(args.env_file)

    # Resolve URLs from CLI > config > env vars
    es_url = (args.es_url or os.environ.get("ES_URL", "")).strip().rstrip("/")
    cribl_url = (args.cribl_url or os.environ.get("CRIBL_URL", "")).strip().rstrip("/")
    result_index = (args.result_index or os.environ.get("ES_INDEX", DEFAULT_RESULT_INDEX)).strip()

    if not es_url:
        sys.exit("ERROR: --es-url or ES_URL required (via CLI, config, or env)")
    if not cribl_url:
        sys.exit("ERROR: --cribl-url or CRIBL_URL required (via CLI, config, or env)")

    verify_ssl = not args.no_verify_ssl
    es_session = build_es_session(verify_ssl)
    cribl_session = build_cribl_session(verify_ssl)

    # Step 1: Get all apmIds from ELK
    print(f"\n[1/4] Querying {SOURCE_INDEX} for last {args.days} days...")
    raw_pairs = fetch_all_apmids(es_session, es_url, args.days)
    print(f"  Found {len(raw_pairs)} unique apmId/appName pairs")

    apmids = dedup_by_apmid(raw_pairs)
    print(f"  After dedup: {len(apmids)} unique apmIds")

    # Step 2: Get Cribl destinations + routes
    print(f"\n[2/4] Fetching Cribl destinations & routes (group={args.group})...")
    auth = CriblAuth(cribl_url, cribl_session)
    client = CriblClient(cribl_url, args.group, auth, cribl_session)

    destinations = client.list_azure_blob_outputs()
    print(f"  Found {len(destinations)} azure_blob destination(s)")
    for d in destinations:
        print(f"    {d.get('id', '?'):30s}  container={d.get('containerName', '')!r}")

    routes = client.list_routes()
    print(f"  Found {len(routes)} route(s)")

    # Step 3: Compare
    print(f"\n[3/4] Matching apmIds to destinations/routes (mode={args.match_mode})...")
    results = check_route_dest_status(apmids, destinations, routes, args.match_mode)
    print_status_table(results)

    # Step 4: Index results to ELK
    print(f"\n[4/4] Indexing {len(results)} results to {result_index}...")
    indexed = index_to_elk(es_session, es_url, result_index, results, args.group)
    print(f"  Indexed {indexed} doc(s) to {result_index}")

    # Optional CSV output
    if args.output:
        fieldnames = ["apmId", "appName", "has_destination", "destination_id",
                       "has_route", "route_id", "route_output", "status"]
        with open(args.output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"  CSV saved to {args.output}")


if __name__ == "__main__":
    main()
