#!/usr/bin/env python3
"""
Get all unique apmId/appName from Azure Blob Storage default container,
compare with Cribl azure_blob destinations and routes, output results.

Replaces get_apmids_from_elk.py — reads directly from blob instead of ELK.

Blob path pattern:
  {container}/{YYYY}/{MM}/{DD}/{appName}/{region}/{env}/CriblOut-*.json.gz

Flow:
  1. List blobs in default container (with optional date/region/env filters)
  2. Extract appName from path, apmId from gzipped JSON content
  3. Fetch azure_blob destinations + routes from Cribl API
  4. Match: apmId in containerName (destination), apmId in route name (route)
  5. Output results to CSV and console

Usage:
  python get_apmids_from_blob.py --config config.json
  python get_apmids_from_blob.py --config config.json --days 7 -o results.csv
  python get_apmids_from_blob.py --config config.json --region eastus --env prod
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from azure.storage.blob import BlobServiceClient, ContainerClient
except ImportError:
    sys.exit(
        "ERROR: azure-storage-blob is required.\n"
        "  pip install azure-storage-blob"
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CONTAINER = "default"
BLOB_PREFIX_DATE_FMT = "%Y/%m/%d"
CRIBL_CLOUD_LOGIN_URL = "https://login.cribl.cloud/oauth/token"
CRIBL_CLOUD_AUDIENCE = "https://api.cribl.cloud"
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30

# ---------------------------------------------------------------------------
# Config file loader
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"ERROR: config file not found: {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: invalid JSON in {path}: {exc}")
    if not isinstance(raw, dict):
        sys.exit(f"ERROR: config file must be a JSON object, got {type(raw).__name__}")
    return raw


# ---------------------------------------------------------------------------
# HTTP session (for Cribl API)
# ---------------------------------------------------------------------------

def build_cribl_session(verify_ssl: bool = True) -> requests.Session:
    s = requests.Session()
    s.verify = verify_ssl
    if not verify_ssl:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    retries = Retry(
        total=3, backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


# ---------------------------------------------------------------------------
# Cribl Auth
# ---------------------------------------------------------------------------

class CriblAuth:
    def __init__(self, cribl_url: str, session: requests.Session, creds: dict[str, str]) -> None:
        self._cribl_url = cribl_url.rstrip("/")
        self._session = session
        self._creds = creds
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
        client_id = self._creds.get("client_id", "").strip()
        client_secret = self._creds.get("client_secret", "").strip()
        username = self._creds.get("username", "").strip()
        password = self._creds.get("password", "").strip()
        static_token = self._creds.get("token", "").strip()

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
                "ERROR: No Cribl credentials in config.json auth section. "
                "Provide client_id+client_secret, username+password, or token."
            )


# ---------------------------------------------------------------------------
# Cribl API client (read-only)
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
        if not isinstance(data, dict):
            return data

        routes: list[dict[str, Any]] = []
        items = data.get("items", [])
        for item in items:
            if isinstance(item, dict) and "routes" in item and isinstance(item["routes"], list):
                routes.extend(item["routes"])
            elif isinstance(item, dict) and ("name" in item or "filter" in item):
                routes.append(item)

        if not routes and "routes" in data:
            routes = data["routes"]
        if not routes and "groups" in data:
            for g in data["groups"].values():
                if isinstance(g, dict):
                    routes.extend(g.get("routes", []))

        return routes


# ---------------------------------------------------------------------------
# Step 1: Azure Blob — list and read blobs from default container
# ---------------------------------------------------------------------------

def _build_service_principal_credential(blob_cfg: dict[str, str]):
    """Build a ClientSecretCredential from tenant_id, client_id, client_secret."""
    tenant_id = blob_cfg.get("tenant_id", "").strip()
    client_id = blob_cfg.get("client_id", "").strip()
    client_secret = blob_cfg.get("client_secret", "").strip()

    if not (tenant_id and client_id and client_secret):
        return None

    try:
        from azure.identity import ClientSecretCredential
    except ImportError:
        sys.exit(
            "ERROR: azure-identity is required for service principal auth.\n"
            "  pip install azure-identity"
        )
    return ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )


def build_blob_client(blob_cfg: dict[str, str]) -> ContainerClient:
    """Build an Azure ContainerClient from config.

    Auth priority:
      1. connection_string
      2. Service principal (tenant_id + client_id + client_secret)
      3. account_url / account_name + sas_token / account_key
      4. DefaultAzureCredential (managed identity / az login)
    """
    connection_string = blob_cfg.get("connection_string", "").strip()
    account_url = blob_cfg.get("account_url", "").strip()
    account_name = blob_cfg.get("account_name", "").strip()
    account_key = blob_cfg.get("account_key", "").strip()
    sas_token = blob_cfg.get("sas_token", "").strip()
    container = blob_cfg.get("container", DEFAULT_CONTAINER)

    if connection_string:
        service = BlobServiceClient.from_connection_string(connection_string)
        return service.get_container_client(container)

    # Service principal auth — works with account_url or account_name
    sp_credential = _build_service_principal_credential(blob_cfg)

    url = account_url
    if not url and account_name:
        url = f"https://{account_name}.blob.core.windows.net"

    if url:
        if sp_credential:
            service = BlobServiceClient(account_url=url, credential=sp_credential)
        elif sas_token:
            service = BlobServiceClient(account_url=url, credential=sas_token)
        elif account_key:
            service = BlobServiceClient(account_url=url, credential=account_key)
        else:
            # Fall back to DefaultAzureCredential (managed identity / az login)
            try:
                from azure.identity import DefaultAzureCredential
                service = BlobServiceClient(account_url=url, credential=DefaultAzureCredential())
            except ImportError:
                sys.exit(
                    "ERROR: azure-identity is required for managed identity auth.\n"
                    "  pip install azure-identity\n"
                    "  Or provide account_key, sas_token, or service principal creds in config."
                )
        return service.get_container_client(container)

    sys.exit(
        "ERROR: blob_storage config must include one of:\n"
        "  - connection_string\n"
        "  - account_url (+ service principal / sas_token / account_key)\n"
        "  - account_name (+ service principal / account_key / sas_token)"
    )


def generate_date_prefixes(days: int) -> list[str]:
    """Generate date-based prefixes for the last N days: YYYY/MM/DD"""
    today = datetime.now(timezone.utc).date()
    prefixes = []
    for i in range(days):
        d = today - timedelta(days=i)
        prefixes.append(d.strftime(BLOB_PREFIX_DATE_FMT))
    return prefixes



def _extract_apmid_from_blob(
    container_client: ContainerClient, blob_name: str, app_name_dir: str, debug: bool = False,
) -> tuple[str, str] | None:
    """Download one blob, scan up to 10 JSON lines for apmId. Returns (apmId, appName) or None."""
    blob_data = container_client.download_blob(blob_name).readall()
    with gzip.open(io.BytesIO(blob_data), "rt", encoding="utf-8") as gz:
        lines_checked = 0
        for line in gz:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            lines_checked += 1

            apm_id = event.get("apmId")
            if apm_id:
                app_name = event.get("appName") or app_name_dir
                if debug:
                    print(f"      {blob_name} -> apmId={apm_id} (line {lines_checked})")
                return (str(apm_id), str(app_name))

            if debug and lines_checked <= 3:
                sample = json.dumps(event, default=str)[:300]
                print(f"      {blob_name} line {lines_checked} keys={sorted(event.keys())}")
                print(f"        sample: {sample}")

            if lines_checked >= 10:
                break

    if debug:
        print(f"      {blob_name} -> no apmId in {lines_checked} lines checked")
    return None


def fetch_apmids_from_blob(
    container_client: ContainerClient,
    days: int = 1,
    region_filter: str | None = None,
    env_filter: str | None = None,
    max_blobs: int = 0,
    max_workers: int = 10,
    debug: bool = False,
) -> list[dict]:
    """
    List ALL CriblOut-*.json.gz blobs under each date prefix.
    Group by parent folder, pick the largest file per folder, download it
    in parallel to extract apmId from the JSON content.

    No assumptions about path depth — works with any folder structure.

    Returns list of dicts: [{apmId, appName, event_count}, ...]
    """
    date_prefixes = generate_date_prefixes(days)
    counter: Counter = Counter()  # (apmId, appName) -> count
    blobs_processed = 0
    blobs_errors = 0

    for date_prefix in date_prefixes:
        print(f"  Scanning date: {date_prefix}")

        # Phase 1: List ALL CriblOut files under this date, group by parent folder.
        # folder_key = everything before the filename
        # e.g. "2026/06/18/app/sub/region/env/" -> pick best file per folder
        folders: dict[str, tuple[str, int]] = {}  # folder -> (best_blob_name, best_size)
        total_listed = 0
        skipped_small = 0
        min_size = 50  # empty gzip stubs are ~20 bytes

        print(f"    Listing all blobs (this may take a moment)...")
        for blob in container_client.list_blobs(name_starts_with=f"{date_prefix}/"):
            total_listed += 1
            bname = blob.name
            if not bname.endswith(".json.gz"):
                continue
            filename = bname.split("/")[-1]
            if not filename.startswith("CriblOut-"):
                continue

            size = blob.size or 0
            if size < min_size:
                skipped_small += 1
                continue

            # Apply region filter if set — check if region appears in path
            if region_filter and f"/{region_filter.lower()}/" not in bname.lower():
                continue
            if env_filter and f"/{env_filter.lower()}/" not in bname.lower():
                continue

            # Group by parent folder (path without filename)
            folder_key = bname.rsplit("/", 1)[0]
            if folder_key not in folders or size > folders[folder_key][1]:
                folders[folder_key] = (bname, size)

        print(
            f"    Listed {total_listed} blobs, "
            f"{len(folders)} folders with CriblOut files, "
            f"{skipped_small} empty files skipped"
        )

        if not folders:
            print(f"    No CriblOut files found for {date_prefix}")
            continue

        # Phase 2: Download ONE file per folder in parallel, extract apmId
        total_folders = len(folders)
        folder_items = sorted(folders.items())
        if max_blobs:
            folder_items = folder_items[:max_blobs]

        print(f"    Downloading {len(folder_items)} files ({max_workers} threads)...")
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_info = {
                pool.submit(
                    _extract_apmid_from_blob, container_client, bname, folder_key, debug
                ): (folder_key, bname, size)
                for folder_key, (bname, size) in folder_items
            }
            for future in as_completed(future_to_info):
                folder_key, bname, size = future_to_info[future]
                completed += 1
                try:
                    result = future.result()
                    blobs_processed += 1
                    if result:
                        apm_id, app_name = result
                        counter[(apm_id, app_name)] += 1
                        print(
                            f"    [{completed}/{total_folders}] apmId={apm_id} "
                            f"(size={size}) | total: {len(counter)} unique apmIds"
                        )
                    else:
                        if debug:
                            print(f"    [{completed}/{total_folders}] no apmId in {bname} (size={size})")
                except Exception as exc:
                    blobs_errors += 1
                    print(f"    [{completed}/{total_folders}] ERROR {bname}: {exc}", file=sys.stderr)

        print(f"    {blobs_processed} blobs processed total so far")

    print(f"\n  Summary: {blobs_processed} blobs processed, {blobs_errors} errors, {len(counter)} unique apmIds")

    # Deduplicate — keep highest count per apmId
    best: dict[str, dict] = {}
    for (apm_id, app_name), count in counter.items():
        if apm_id not in best or count > best[apm_id]["event_count"]:
            best[apm_id] = {"apmId": apm_id, "appName": app_name, "event_count": count}

    return sorted(best.values(), key=lambda r: r["apmId"])


# ---------------------------------------------------------------------------
# Step 2: Matching (same logic as ELK script)
# ---------------------------------------------------------------------------

def check_route_dest_status(
    apmids: list[dict],
    destinations: list[dict[str, Any]],
    routes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results = []
    for row in sorted(apmids, key=lambda r: r["apmId"]):
        app_id = row["apmId"]
        app_name = row["appName"]
        app_lower = app_id.lower()

        # Destination: apmId appears in containerName
        dest_id = None
        for dest in destinations:
            container = (dest.get("containerName") or "").lower()
            if app_lower in container:
                dest_id = dest.get("id", "?")
                break

        # Route: apmId appears in route name
        route_match_id = None
        for route in routes:
            route_name = str(route.get("name", "")).lower()
            if app_lower in route_name:
                route_match_id = route.get("id", route.get("name", "?"))
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
            "event_count": row.get("event_count", 0),
            "has_destination": has_dest,
            "destination_id": dest_id or "NONE",
            "has_route": has_route,
            "route_id": route_match_id or "NONE",
            "status": status,
        })

    return results


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_status_table(results: list[dict]) -> None:
    if not results:
        print("\n  No apmIds found.")
        return

    apm_w = max(max(len(r["apmId"]) for r in results), 5)
    app_w = max(max(len(r["appName"]) for r in results), 7)
    dest_w = max(max(len(r["destination_id"]) for r in results), 14)
    route_w = max(max(len(r["route_id"]) for r in results), 8)

    header = (
        f"{'apmId':<{apm_w}s}   {'appName':<{app_w}s}   {'Events':>6s}   {'Has Dest':>8s}   "
        f"{'Destination':<{dest_w}s}   {'Has Route':>9s}   {'Route':<{route_w}s}   Status"
    )
    print(f"\n{header}")
    print("-" * len(header))

    for r in results:
        dest_flag = "YES" if r["has_destination"] else "NO"
        route_flag = "YES" if r["has_route"] else "NO"
        marker = "" if r["status"] == "CONFIGURED" else " <<<"
        print(
            f"{r['apmId']:<{apm_w}s}   {r['appName']:<{app_w}s}   {r['event_count']:>6d}   "
            f"{dest_flag:>8s}   {r['destination_id']:<{dest_w}s}   {route_flag:>9s}   "
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


def save_csv(results: list[dict], output_path: str) -> None:
    fieldnames = [
        "apmId", "appName", "event_count", "has_destination",
        "destination_id", "has_route", "route_id", "status",
    ]
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"  CSV saved to {output_path}")

    # Additional report — missing only
    missing = [r for r in results if r["status"] != "CONFIGURED"]
    if missing:
        base, ext = os.path.splitext(output_path)
        missing_path = f"{base}_missing_only{ext}"
        with open(missing_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["apmId", "appName", "event_count", "status"],
                                    extrasaction="ignore")
            writer.writeheader()
            writer.writerows(missing)
        print(f"  Missing apmIds CSV saved to {missing_path} ({len(missing)} entries)")


def save_json(results: list[dict], output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  JSON saved to {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Get apmIds from Azure Blob default container, compare with Cribl destinations/routes"
    )
    parser.add_argument("--config", required=True, help="Path to config.json")
    parser.add_argument("--days", type=int, default=1, help="Look back N days (default: 1 = today only)")
    parser.add_argument("--region", help="Filter blobs by region (e.g. eastus)")
    parser.add_argument("--env", help="Filter blobs by environment (e.g. prod, dev)")
    parser.add_argument("--max-blobs", type=int, default=0, help="Max blobs to process (0=unlimited)")
    parser.add_argument("--workers", type=int, default=10, help="Parallel download threads (default: 10)")
    parser.add_argument("--output", "-o", help="Save CSV to file")
    parser.add_argument("--json-output", help="Save JSON to file")
    parser.add_argument("--debug", action="store_true", help="Print debug info")
    args = parser.parse_args()

    cfg = load_config(args.config)

    # --- Cribl config ---
    auth_cfg = cfg.get("auth", {})
    cribl_url = auth_cfg.get("cribl_url", "").strip().rstrip("/")
    if not cribl_url:
        sys.exit("ERROR: auth.cribl_url is required in config.json")

    capture_cfg = cfg.get("capture", {})
    groups = capture_cfg.get("groups", ["default"])
    group = groups[0] if isinstance(groups, list) and groups else "default"

    # --- Blob storage config ---
    blob_cfg = cfg.get("blob_storage", {})
    if not blob_cfg:
        sys.exit(
            "ERROR: blob_storage section is required in config.json.\n"
            "  Example:\n"
            '  "blob_storage": {\n'
            '    "connection_string": "DefaultEndpointsProtocol=https;AccountName=...",\n'
            '    "container": "default"\n'
            "  }"
        )

    # --- Connection ---
    conn_cfg = cfg.get("connection", {})
    verify_ssl = conn_cfg.get("verify_ssl", True)

    # Step 1: Read blobs
    print(f"\n[1/3] Connecting to Azure Blob Storage...")
    container_client = build_blob_client(blob_cfg)
    container_name = blob_cfg.get("container", DEFAULT_CONTAINER)
    print(f"  Container: {container_name}")
    print(f"  Scanning last {args.days} day(s)")
    if args.region:
        print(f"  Region filter: {args.region}")
    if args.env:
        print(f"  Env filter: {args.env}")

    apmids = fetch_apmids_from_blob(
        container_client,
        days=args.days,
        region_filter=args.region,
        env_filter=args.env,
        max_blobs=args.max_blobs,
        max_workers=args.workers,
        debug=args.debug,
    )
    print(f"\n  Found {len(apmids)} unique apmIds")

    if not apmids:
        print("\n  No apmIds found in blob data. Nothing to do.")
        sys.exit(0)

    if args.debug:
        print(f"\n  DEBUG: First 10 apmIds:")
        for i, r in enumerate(apmids[:10]):
            print(f"    [{i}] apmId={r['apmId']!r}  appName={r['appName']!r}  events={r['event_count']}")

    # Step 2: Get Cribl destinations + routes
    print(f"\n[2/3] Fetching Cribl destinations & routes (group={group})...")
    cribl_session = build_cribl_session(verify_ssl)
    auth = CriblAuth(cribl_url, cribl_session, auth_cfg)
    client = CriblClient(cribl_url, group, auth, cribl_session)

    destinations = client.list_azure_blob_outputs()
    print(f"  Found {len(destinations)} azure_blob destination(s)")
    for d in destinations:
        print(f"    {d.get('id', '?'):30s}  container={d.get('containerName', '')!r}")

    routes = client.list_routes()
    print(f"  Found {len(routes)} route(s)")

    # Step 3: Compare
    print(f"\n[3/3] Matching apmIds to destinations/routes...")
    results = check_route_dest_status(apmids, destinations, routes)
    print_status_table(results)

    # Output
    if args.output:
        save_csv(results, args.output)
    if args.json_output:
        save_json(results, args.json_output)

    # Summary exit
    missing_count = sum(1 for r in results if r["status"] != "CONFIGURED")
    if missing_count:
        print(f"\n  WARNING: {missing_count} apmId(s) are not fully configured in Cribl.")
    else:
        print(f"\n  All {len(results)} apmIds are fully configured.")


if __name__ == "__main__":
    main()
