"""Core analysis — inspect, dry-run, and full analysis modes."""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from .client import CriblClient
from .constants import EXIT_ERROR, EXIT_OK, EXIT_PARTIAL
from .elasticsearch import build_es_client
from .exceptions import AuthenticationError, CriblAPIError
from .lookup import find_latest_csv, load_all_known_appids, load_lookup_appids, load_previous_unmatched
from .matching import check_lookup_route_dest_status, extract_appid, get_nested, match_appid_to_dest
from .output import (
    ResultRow,
    print_lookup_status_table,
    print_results_table,
    write_csv,
    write_json,
    write_lookup_status_csv,
)

log = logging.getLogger("cribl_audit")

_print_lock = threading.Lock()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def resolve_default_output_id(client: CriblClient) -> str | None:
    """Resolve the default output ID from env var or Cribl config."""
    env_val = os.environ.get("CRIBL_DEFAULT_OUTPUT_ID", "").strip()
    if env_val:
        return env_val
    return client.find_default_output_id()


def _progress_capture(
    client: CriblClient,
    *,
    filter_expr: str,
    duration: int,
    max_events: int,
    level: int,
) -> list[dict[str, Any]]:
    """Capture with inline progress indicator."""
    print(
        f"  Capturing for up to {duration}s (level={level}, "
        f"max={max_events}) ...",
        end="", flush=True,
    )
    t0 = time.monotonic()
    events = client.capture_live(
        filter_expr=filter_expr, duration=duration,
        max_events=max_events, level=level,
    )
    elapsed = time.monotonic() - t0
    print(f" done ({elapsed:.1f}s, {len(events)} events)")
    return events


# ------------------------------------------------------------------
# Inspect mode
# ------------------------------------------------------------------

def run_inspect(client: CriblClient, appid_field: str, level: int) -> None:
    """Discovery mode — sample events, show fields, suggest filters."""
    print("=" * 70)
    print(f"INSPECTION MODE  (group: {client.group})")
    print("=" * 70)

    default_id = resolve_default_output_id(client)

    print("\n[1/3] Detecting default output ID\n")
    if default_id:
        print(f"  Default output ID: {default_id!r}")
    else:
        print("  Could not auto-detect default output ID.")

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


# ------------------------------------------------------------------
# Dry run
# ------------------------------------------------------------------

def run_dry_run(clients: list[CriblClient], args: argparse.Namespace) -> int:
    """Validate connectivity, auth, and config without capturing events."""
    print("=" * 70)
    print("DRY RUN — validating configuration (no events will be captured)")
    print("=" * 70)

    groups = [c.group for c in clients]
    ok = True

    print("\n[1/4] Authentication")
    try:
        _ = clients[0]._auth.token
        print("  OK — token acquired")
    except (AuthenticationError, CriblAPIError) as exc:
        print(f"  FAIL — {exc}")
        ok = False

    print("\n[2/4] Default output ID")
    default_id = args.default_output or resolve_default_output_id(clients[0])
    if default_id:
        print(f"  OK — {default_id!r}")
    else:
        print("  WARN — could not resolve. Will capture ALL events.")

    print(f"\n[3/4] Group connectivity ({len(groups)} group(s))")
    for client in clients:
        try:
            dests = client.list_azure_blob_outputs()
            print(f"  {client.group}: OK — {len(dests)} azure_blob destination(s)")
        except (CriblAPIError, requests.ConnectionError) as exc:
            print(f"  {client.group}: FAIL — {exc}")
            ok = False

    print("\n[4/4] Elasticsearch")
    es_client = build_es_client(args)
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


# ------------------------------------------------------------------
# Per-group capture worker
# ------------------------------------------------------------------

def _analyse_group(
    client: CriblClient,
    args: argparse.Namespace,
    effective_filter: str,
    buffered: bool = False,
) -> tuple[list[ResultRow], set[str], set[str], int, int, int, str]:
    """Run capture rounds for one group.

    Returns ``(rows, app_ids, unmatched_ids, total_events,
    raw_parse_count, missing_count, output_text)``.
    """
    buf = io.StringIO() if buffered else None
    _print = (lambda *a, **kw: print(*a, file=buf, **kw)) if buf else print

    combo_counts: Counter[tuple[str, str, str]] = Counter()
    raw_parse_count = 0
    missing_count = 0
    total_events = 0
    interrupted = False

    try:
        for rnd in range(1, args.rounds + 1):
            if args.rounds > 1:
                _print(f"\n  --- Round {rnd}/{args.rounds} ({client.group}) ---")

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
                rnd, args.rounds, client.group, len(events), elapsed,
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

            if args.rounds > 1:
                app_ids_so_far = {apm_id for apm_id, _, _ in combo_counts}
                _print(
                    f"  Cumulative: {len(app_ids_so_far)} distinct "
                    f"{args.appid_field} values, {total_events} events"
                )

            if rnd < args.rounds:
                _print(f"  Waiting {args.interval}s before next round ...", end="", flush=True)
                time.sleep(args.interval)
                _print(" done")

    except KeyboardInterrupt:
        interrupted = True
        _print(f"\n  Interrupted after {total_events} events — saving partial results ...")

    app_ids = {apm_id for apm_id, _, _ in combo_counts}

    if total_events == 0:
        output_text = buf.getvalue() if buf else ""
        return [], set(), set(), 0, raw_parse_count, missing_count, output_text

    destinations = client.list_azure_blob_outputs()
    log.info("[%s] %d azure_blob destinations found", client.group, len(destinations))
    _print(f"  Found {len(destinations)} azure_blob destination(s) ({client.group}).")
    if destinations:
        id_width = max(len(d.get("id", "")) for d in destinations)
        for d in destinations:
            _print(
                f"    {d['id']:<{id_width}s}  "
                f"container={d.get('containerName', '')!r}"
            )

    rows: list[ResultRow] = []
    for (apm_id, app_name, output_id), count in sorted(combo_counts.items()):
        dest = match_appid_to_dest(apm_id, destinations, args.match_mode)
        matched = dest if dest else "DEFAULT"
        rows.append((apm_id, app_name, output_id, matched, count))

    unmatched_ids = {r[0] for r in rows if r[3] == "DEFAULT"}
    log.info(
        "[%s] Done: %d events, %d distinct appIds, %d unmatched, %d from _raw, %d missing field",
        client.group, total_events, len(app_ids), len(unmatched_ids),
        raw_parse_count, missing_count,
    )
    output_text = buf.getvalue() if buf else ""

    if interrupted:
        raise KeyboardInterrupt

    return rows, app_ids, unmatched_ids, total_events, raw_parse_count, missing_count, output_text


# ------------------------------------------------------------------
# Full analysis
# ------------------------------------------------------------------

def run_analysis(clients: list[CriblClient], args: argparse.Namespace) -> int:
    """Run analysis across all groups. Returns exit code."""
    groups = [c.group for c in clients]
    log.info(
        "Starting analysis: groups=%s, rounds=%d, seconds=%d, interval=%d",
        groups, args.rounds, args.seconds, args.interval,
    )

    default_id = args.default_output or resolve_default_output_id(clients[0])

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

    all_rows: list[ResultRow] = []
    all_app_ids: set[str] = set()
    all_unmatched: set[str] = set()
    grand_total_events = 0
    grand_raw_parse = 0
    grand_missing = 0
    failed_groups: list[str] = []

    print(f"\n[Step 1/3] Live capture ({args.rounds} round(s), "
          f"{args.seconds}s each, groups: {', '.join(groups)})")

    if len(clients) > 1:
        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            futures = {
                pool.submit(_analyse_group, client, args, effective_filter, True): client.group
                for client in clients
            }
            for future in as_completed(futures):
                group_name = futures[future]
                try:
                    rows, app_ids, unmatched, total_ev, raw_ct, miss_ct, output_text = future.result()
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
            log.error("Group %r failed: %s", clients[0].group, exc)
            failed_groups.append(clients[0].group)

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

    # Load known apmIds from existing CSV
    known_appids: set[str] = set()
    existing_csv = args.diff_csv or find_latest_csv(".", args.output)
    if existing_csv:
        known_appids = load_all_known_appids(existing_csv)

    # Load lookup table
    lookup_appids: set[str] = set()
    if args.lookup:
        lookup_appids = load_lookup_appids(args.lookup)

    excluded = known_appids | {aid for aid in all_app_ids if aid.lower() in lookup_appids}
    new_app_ids = all_app_ids - excluded
    new_rows = [r for r in all_rows if r[0] in new_app_ids] if excluded else all_rows
    new_unmatched = {r[0] for r in new_rows if r[3] == "DEFAULT"}
    in_lookup = {aid for aid in all_app_ids if aid.lower() in lookup_appids}

    # --- Lookup route/destination audit ---
    if default_id:
        lookup_hitting_default = {
            aid for aid in all_app_ids
            if aid.lower() in lookup_appids
            and any(r[0] == aid and default_id in r[2] for r in all_rows)
        }
    else:
        lookup_hitting_default = {
            aid for aid in all_app_ids
            if aid.lower() in lookup_appids
        }

    lookup_status_results: list[dict[str, Any]] = []
    if lookup_hitting_default:
        log.info(
            "%d lookup appId(s) are hitting the default destination — checking route/destination config",
            len(lookup_hitting_default),
        )
        try:
            all_destinations = clients[0].list_azure_blob_outputs()
            all_routes = clients[0].list_routes()
            log.info(
                "Fetched %d destinations, %d routes for lookup status check",
                len(all_destinations), len(all_routes),
            )
            lookup_status_results = check_lookup_route_dest_status(
                lookup_hitting_default, all_destinations, all_routes, args.match_mode,
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

    if lookup_status_results:
        print(f"\n[ALERT] Lookup appIds with containers hitting default destination")
        print(f"These appIds have Azure containers (per lookup table) but are still")
        print(f"falling through to the default output — checking route/destination config:")
        print_lookup_status_table(lookup_status_results)

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

    print(f"\n[Step 3/3] Matching new apmIds (mode={args.match_mode!r})")
    print_results_table(new_rows, new_app_ids, new_unmatched, grand_total_events)

    # Diff against previous CSV
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

    # Write output
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

    # Elasticsearch
    es_client = build_es_client(args)
    if es_client and new_rows:
        log.info("Indexing %d doc(s) to Elasticsearch %s/%s", len(new_rows), es_client._url, es_client._index)
        es_client.index_results(
            new_rows, group=", ".join(groups),
            total_events=grand_total_events, is_new=bool(known_appids),
        )

    exit_code = EXIT_PARTIAL if failed_groups else EXIT_OK
    log.info("Analysis complete — exit code %d", exit_code)
    return exit_code
