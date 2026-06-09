#!/usr/bin/env python3
"""
Get all unique apmId + appName pairs from ELK index logs-k8s-container-all*.

Usage:
  python get_apmids_from_elk.py --es-url https://your-es:9200
  python get_apmids_from_elk.py --es-url https://your-es:9200 --days 7 --output results.csv

Auth via env vars:
  ES_API_KEY          API key (preferred)
  ES_USERNAME / ES_PASSWORD   Basic auth
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import requests

INDEX = "logs-k8s-container-all*"


def build_session(api_key: str | None, username: str | None, password: str | None, verify_ssl: bool) -> requests.Session:
    s = requests.Session()
    s.verify = verify_ssl
    s.headers["Content-Type"] = "application/json"
    if api_key:
        s.headers["Authorization"] = f"ApiKey {api_key}"
    elif username and password:
        s.auth = (username, password)
    return s


def fetch_all_apmids(session: requests.Session, es_url: str, days: int) -> list[dict]:
    """Use composite aggregation to get ALL unique apmId/appName pairs."""
    url = f"{es_url}/{INDEX}/_search"
    results = []
    after = None

    while True:
        # Build composite agg with optional "after" for pagination
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
            "aggs": {
                "unique_pairs": {"composite": composite}
            },
        }

        resp = session.post(url, json=query, timeout=(10, 60))
        if resp.status_code != 200:
            print(f"ERROR: ES returned HTTP {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        buckets = data["aggregations"]["unique_pairs"]["buckets"]
        if not buckets:
            break

        for b in buckets:
            results.append({
                "apmId": b["key"]["apmId"] or "",
                "appName": b["key"]["appName"] or "",
                "doc_count": b["doc_count"],
            })

        after = buckets[-1]["key"]
        print(f"  fetched {len(results)} unique pairs so far...", file=sys.stderr)

    return results


def main():
    parser = argparse.ArgumentParser(description="Get all apmId/appName from ELK")
    parser.add_argument("--es-url", default=os.environ.get("ES_URL", ""), help="Elasticsearch URL")
    parser.add_argument("--days", type=int, default=30, help="Look back N days (default: 30)")
    parser.add_argument("--output", "-o", help="Output CSV file (default: stdout)")
    parser.add_argument("--no-verify-ssl", action="store_true")
    args = parser.parse_args()

    es_url = args.es_url.strip().rstrip("/")
    if not es_url:
        print("ERROR: --es-url or ES_URL required", file=sys.stderr)
        sys.exit(1)

    session = build_session(
        api_key=os.environ.get("ES_API_KEY", "").strip() or None,
        username=os.environ.get("ES_USERNAME", "").strip() or None,
        password=os.environ.get("ES_PASSWORD", "").strip() or None,
        verify_ssl=not args.no_verify_ssl,
    )

    print(f"Querying {INDEX} for last {args.days} days...", file=sys.stderr)
    results = fetch_all_apmids(session, es_url, args.days)
    print(f"\nFound {len(results)} unique apmId/appName pairs", file=sys.stderr)

    # Deduplicate: keep the appName with highest doc_count per apmId
    best: dict[str, dict] = {}
    for row in results:
        apm_id = row["apmId"]
        if apm_id not in best or row["doc_count"] > best[apm_id]["doc_count"]:
            best[apm_id] = row
    deduped = list(best.values())
    print(f"After dedup: {len(deduped)} unique apmIds", file=sys.stderr)

    # Output
    out = open(args.output, "w", newline="") if args.output else sys.stdout
    writer = csv.DictWriter(out, fieldnames=["apmId", "appName", "doc_count"])
    writer.writeheader()
    for row in sorted(deduped, key=lambda r: r["apmId"]):
        writer.writerow(row)

    if args.output:
        out.close()
        print(f"Saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
