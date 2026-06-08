"""Output writers — CSV, JSON, lookup-status CSV, and console tables."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import Any


# ------------------------------------------------------------------
# Result row type: (apmId, appName, outputId, matched_destination, event_count)
# ------------------------------------------------------------------
ResultRow = tuple[str, str, str, str, int]


# ------------------------------------------------------------------
# CSV
# ------------------------------------------------------------------

def write_csv(rows: list[ResultRow], output_path: str, append: bool) -> None:
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


# ------------------------------------------------------------------
# JSON
# ------------------------------------------------------------------

def write_json(rows: list[ResultRow], output_path: str, group: str, total_events: int) -> None:
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


# ------------------------------------------------------------------
# Lookup status CSV
# ------------------------------------------------------------------

def write_lookup_status_csv(results: list[dict[str, Any]], output_path: str) -> None:
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


# ------------------------------------------------------------------
# Console tables
# ------------------------------------------------------------------

def print_results_table(
    rows: list[ResultRow],
    app_ids: set[str],
    unmatched_ids: set[str],
    total_events: int,
) -> None:
    """Print a formatted table of appId results."""
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


def print_lookup_status_table(results: list[dict[str, Any]]) -> None:
    """Print route/destination status for lookup appIds."""
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
