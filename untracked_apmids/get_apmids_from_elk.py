#!/usr/bin/env python3
"""
Get all unique apmId/appName from ELK, compare with Cribl azure_blob
destinations and routes, and index results back into ELK.

Uses the cribl_audit module for Cribl API access (same as find_default_appids.py).

Flow:
  1. Query source ELK for all unique apmId + appName
  2. Fetch azure_blob destinations + routes from Cribl API
  3. Match: apmId in containerName (destination), apmId in route name (route)
  4. Index results into result ELK

Usage:
  python get_apmids_from_elk.py --config config.json
  python get_apmids_from_elk.py --config config.json --days 7 -o results.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import requests

from cribl_audit.auth import CriblAuth
from cribl_audit.client import CriblClient
from cribl_audit.config import load_config as load_cribl_config
from cribl_audit.http import build_session as build_cribl_session

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SOURCE_INDEX = "logs-k8s-container-all*"
DEFAULT_RESULT_INDEX = "cribl-untracked-appids"
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict[str, Any]:
    """Load config.json raw dict + push auth/es creds to env via cribl_audit."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"ERROR: config file not found: {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: invalid JSON in {path}: {exc}")

    if not isinstance(raw, dict):
        sys.exit(f"ERROR: config file must be a JSON object, got {type(raw).__name__}")

    # Use cribl_audit's loader to push auth creds into os.environ
    load_cribl_config(path)

    return raw


# ---------------------------------------------------------------------------
# ES session
# ---------------------------------------------------------------------------

def build_es_session(
    verify_ssl: bool = True,
    api_key: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> requests.Session:
    s = requests.Session()
    s.verify = verify_ssl
    if not verify_ssl:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    s.headers["Content-Type"] = "application/json"
    if api_key:
        s.headers["Authorization"] = f"ApiKey {api_key}"
    elif username and password:
        s.auth = (username, password)
    return s


# ---------------------------------------------------------------------------
# Step 1: Fetch apmIds from ELK
# ---------------------------------------------------------------------------

def fetch_all_apmids(session: requests.Session, es_url: str, source_index: str, days: int) -> list[dict]:
    """Composite aggregation to get ALL unique apmId/appName pairs."""
    url = f"{es_url}/{source_index}/_search"
    results = []
    after = None

    while True:
        sources = [
            {"apmId": {"terms": {"field": "apmId"}}},
            {"appName": {"terms": {"field": "appName", "missing_bucket": True}}},
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
            sys.exit(f"ERROR: ES returned HTTP {resp.status_code}: {resp.text[:1000]}")

        data = resp.json()

        if "aggregations" not in data:
            print(f"\nERROR: ES response has no 'aggregations' key.", file=sys.stderr)
            print(f"  URL: {url}", file=sys.stderr)
            if "error" in data:
                print(f"  Error: {json.dumps(data['error'], indent=2)[:2000]}", file=sys.stderr)
            else:
                print(f"  Response: {json.dumps(data, indent=2)[:2000]}", file=sys.stderr)
            sys.exit(1)

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
        print(f"  fetched {len(results)} unique pairs so far...")

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
# Step 3: Matching
#   Destination: apmId appears in containerName (substring, case-insensitive)
#   Route:       apmId appears in route name (substring, case-insensitive)
# ---------------------------------------------------------------------------

def check_route_dest_status(
    apmids: list[dict],
    destinations: list[dict[str, Any]],
    routes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Check each apmId against Cribl blob containers and routes."""
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
            "has_destination": has_dest,
            "destination_id": dest_id or "NONE",
            "has_route": has_route,
            "route_id": route_match_id or "NONE",
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
    parser = argparse.ArgumentParser(
        description="Get apmIds from ELK, compare with Cribl destinations/routes, store results")
    parser.add_argument("--config", required=True, help="Path to config.json")
    parser.add_argument("--days", type=int, default=30, help="Look back N days (default: 30)")
    parser.add_argument("--output", "-o", help="Also save CSV to file")
    parser.add_argument("--debug", action="store_true", help="Print debug info")
    args = parser.parse_args()

    # Load config (also pushes auth creds to os.environ for cribl_audit)
    cfg = load_config(args.config)

    # Cribl
    auth_cfg = cfg.get("auth", {})
    cribl_url = auth_cfg.get("cribl_url", "").strip().rstrip("/")
    if not cribl_url:
        cribl_url = os.environ.get("CRIBL_URL", "").strip().rstrip("/")
    if not cribl_url:
        sys.exit("ERROR: auth.cribl_url is required in config.json")

    capture_cfg = cfg.get("capture", {})
    groups = capture_cfg.get("groups", ["default"])
    group = groups[0] if isinstance(groups, list) and groups else "default"

    # Source ELK (where to READ apmIds)
    src_es_cfg = cfg.get("source_elasticsearch", {})
    source_es_url = src_es_cfg.get("url", "").strip().rstrip("/")
    source_index = src_es_cfg.get("index", DEFAULT_SOURCE_INDEX).strip()
    source_es_api_key = src_es_cfg.get("api_key", "").strip() or None
    source_es_username = src_es_cfg.get("username", "").strip() or None
    source_es_password = src_es_cfg.get("password", "").strip() or None
    if not source_es_url:
        sys.exit("ERROR: source_elasticsearch.url is required in config.json")

    # Result ELK (where to WRITE results)
    result_es_cfg = cfg.get("elasticsearch", {})
    result_es_url = result_es_cfg.get("url", "").strip().rstrip("/") or source_es_url
    result_index = result_es_cfg.get("index", DEFAULT_RESULT_INDEX).strip()
    result_es_api_key = result_es_cfg.get("api_key", "").strip() or None
    result_es_username = result_es_cfg.get("username", "").strip() or None
    result_es_password = result_es_cfg.get("password", "").strip() or None

    # Connection
    conn_cfg = cfg.get("connection", {})
    verify_ssl = conn_cfg.get("verify_ssl", True)

    # --- Build sessions ---
    source_es_session = build_es_session(
        verify_ssl, api_key=source_es_api_key,
        username=source_es_username, password=source_es_password,
    )
    result_es_session = build_es_session(
        verify_ssl, api_key=result_es_api_key,
        username=result_es_username, password=result_es_password,
    )
    cribl_session = build_cribl_session(verify_ssl)

    # --- Step 1: Get all apmIds from source ELK ---
    print(f"\n[1/4] Querying {source_es_url}/{source_index} for last {args.days} days...")
    raw_pairs = fetch_all_apmids(source_es_session, source_es_url, source_index, args.days)
    print(f"  Found {len(raw_pairs)} unique apmId/appName pairs")

    if args.debug:
        print(f"\n  DEBUG: First 10 raw pairs from ELK:")
        for i, p in enumerate(raw_pairs[:10]):
            print(f"    [{i}] apmId={p['apmId']!r}  appName={p['appName']!r}  count={p['_doc_count']}")

    apmids = dedup_by_apmid(raw_pairs)
    print(f"  After dedup: {len(apmids)} unique apmIds")

    # --- Step 2: Get Cribl destinations + routes (via cribl_audit module) ---
    print(f"\n[2/4] Fetching Cribl destinations & routes (group={group})...")
    auth = CriblAuth(cribl_url, cribl_session)
    client = CriblClient(cribl_url, group, auth, cribl_session)

    destinations = client.list_azure_blob_outputs()
    print(f"  Found {len(destinations)} azure_blob destination(s)")
    for d in destinations:
        print(f"    {d.get('id', '?'):30s}  container={d.get('containerName', '')!r}")

    routes = client.list_routes()
    print(f"  Found {len(routes)} route(s)")
    if args.debug:
        print(f"\n  DEBUG: First 20 routes:")
        for i, r in enumerate(routes[:20]):
            print(f"    [{i}] id={r.get('id', '?')!r}  name={r.get('name', '?')!r}")

    # --- Step 3: Compare ---
    print(f"\n[3/4] Matching apmIds to destinations/routes...")
    results = check_route_dest_status(apmids, destinations, routes)
    print_status_table(results)

    # --- Step 4: Index results to result ELK ---
    print(f"\n[4/4] Indexing {len(results)} results to {result_es_url}/{result_index}...")
    indexed = index_to_elk(result_es_session, result_es_url, result_index, results, group)
    print(f"  Indexed {indexed} doc(s) to {result_index}")

    # Optional CSV output
    if args.output:
        fieldnames = ["apmId", "appName", "has_destination", "destination_id",
                       "has_route", "route_id", "status"]
        with open(args.output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"  CSV saved to {args.output}")


if __name__ == "__main__":
    main()
