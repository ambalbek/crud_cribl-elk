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
import json
import logging
import os
import sys
import textwrap
import time
from collections import Counter
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

# Hardcoded default output ID — the catch-all azure_blob destination.
# Set to None to use auto-detection from the Cribl config.
DEFAULT_OUTPUT_ID = "default"

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

    @property
    def token(self) -> str:
        if self._token and time.time() < self._expires_at:
            return self._token
        self._authenticate()
        if not self._token:
            raise AuthenticationError("Authentication succeeded but no token was returned.")
        return self._token

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
        data = resp.json()
        self._token = data.get("token") or data["access_token"]
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
        data = resp.json()
        return data.get("items", data) if isinstance(data, dict) else data

    def list_azure_blob_outputs(self) -> list[dict[str, Any]]:
        return [o for o in self.list_outputs() if o.get("type") == "azure_blob"]

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


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def run_inspect(client: CriblClient, appid_field: str, level: int) -> None:
    print("=" * 70)
    print("INSPECTION MODE")
    print("=" * 70)

    # --- default output ID ---
    print("\n[1/3] Detecting default output ID\n")
    default_id = DEFAULT_OUTPUT_ID or client.find_default_output_id()
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
# Full analysis
# ---------------------------------------------------------------------------


def run_analysis(client: CriblClient, args: argparse.Namespace) -> None:
    # Auto-build filter if user left it at default
    effective_filter = args.filter
    if effective_filter == "true":
        default_id = DEFAULT_OUTPUT_ID or client.find_default_output_id()
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

    # Step 1 — capture
    print(f"\n[Step 1/3] Live capture")
    events = _progress_capture(
        client,
        filter_expr=effective_filter,
        duration=args.seconds,
        max_events=args.max_events,
        level=args.level,
    )

    if not events:
        print(
            "\nNo events captured. Possible reasons:\n"
            "  - No traffic hitting the default output right now\n"
            "  - Filter expression doesn't match (run --inspect to check)\n"
            "  - Duration too short (try --seconds 60)"
        )
        return

    # Extract distinct appIds with counts
    appid_counts: Counter[str] = Counter()
    raw_parse_count = 0
    missing_count = 0
    for ev in events:
        top_val = get_nested(ev, args.appid_field)
        val = extract_appid(ev, args.appid_field)
        if val is not None:
            appid_counts[val] += 1
            if top_val is None:
                raw_parse_count += 1
        else:
            missing_count += 1

    app_ids = set(appid_counts.keys())
    print(f"  Distinct {args.appid_field} values: {len(app_ids)}")
    if raw_parse_count:
        print(f"  ({raw_parse_count} extracted from _raw JSON)")
    if missing_count:
        print(f"  ({missing_count} events had no {args.appid_field})")

    if not app_ids:
        print(
            f"\nNo {args.appid_field} values found in {len(events)} events."
            f"\nRun with --inspect to examine event structure."
        )
        return

    # Step 2 — list dedicated destinations
    print(f"\n[Step 2/3] Fetching azure_blob destinations")
    destinations = client.list_azure_blob_outputs()
    print(f"  Found {len(destinations)} azure_blob destination(s).")
    if destinations:
        id_width = max(len(d.get("id", "")) for d in destinations)
        for d in destinations:
            print(
                f"    {d['id']:<{id_width}s}  "
                f"container={d.get('containerName', '')!r}"
            )

    # Step 3 — diff
    print(f"\n[Step 3/3] Matching (mode={args.match_mode!r})")
    results: dict[str, str] = {}
    unmatched: list[str] = []
    for aid in sorted(app_ids):
        dest = match_appid_to_dest(aid, destinations, args.match_mode)
        if dest:
            results[aid] = dest
        else:
            results[aid] = "DEFAULT"
            unmatched.append(aid)

    # Table with event counts
    appid_width = max((len(a) for a in results), default=10)
    appid_width = max(appid_width, 5)
    print(
        f"\n{'appId':<{appid_width}s}   {'Events':>6s}   Matched Destination"
    )
    print("-" * (appid_width + 50))
    for aid, dest in results.items():
        marker = " <<<" if dest == "DEFAULT" else ""
        print(
            f"{aid:<{appid_width}s}   {appid_counts[aid]:>6d}   {dest}{marker}"
        )

    print(f"\nTotal distinct appIds : {len(app_ids)}")
    print(f"Matched               : {len(app_ids) - len(unmatched)}")
    print(f"Unmatched (DEFAULT)   : {len(unmatched)}")
    print(f"Total events captured : {len(events)}")

    # CSV
    write_mode = "a" if args.append else "w"
    file_exists = args.append and os.path.isfile(args.output)

    if args.append and file_exists:
        # Read existing appIds to deduplicate
        existing: set[str] = set()
        with open(args.output, newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                existing.add(row.get("appId", ""))
        new_unmatched = [a for a in unmatched if a not in existing]
        with open(args.output, "a", newline="") as fh:
            writer = csv.writer(fh)
            for aid in new_unmatched:
                writer.writerow([aid, appid_counts[aid]])
        print(
            f"\nAppended {len(new_unmatched)} new appId(s) to {args.output}"
            f" ({len(unmatched) - len(new_unmatched)} already present)"
        )
    else:
        with open(args.output, write_mode, newline="") as fh:
            writer = csv.writer(fh)
            if write_mode == "w" or not file_exists:
                writer.writerow(["appId", "event_count"])
            for aid in unmatched:
                writer.writerow([aid, appid_counts[aid]])
        print(f"\nUnmatched appIds written to {args.output}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
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

            Auth (environment variables, checked in order):
              CRIBL_CLIENT_ID + CRIBL_CLIENT_SECRET   Cribl Cloud OAuth2
              CRIBL_USERNAME  + CRIBL_PASSWORD         Self-managed leader
              CRIBL_TOKEN                              Pre-existing bearer token
              CRIBL_URL                                Always required

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
              python find_default_appids.py --group mygroup --inspect

              # 2. Full analysis (auto-detects default output filter)
              python find_default_appids.py --group mygroup --seconds 60

              # 3. Run again later and accumulate results
              python find_default_appids.py --group mygroup --seconds 60 --append

              # 4. Or with explicit filter
              python find_default_appids.py --group mygroup \\
                --filter "__outputId==='my_default_blob'" --seconds 60
        """),
    )
    parser.add_argument("--group", required=True, help="Cribl worker group name")
    parser.add_argument(
        "--filter",
        default="true",
        help=(
            "JavaScript filter for capture. If omitted, auto-detects the "
            "default output and filters to it. (default: auto-detect)"
        ),
    )
    parser.add_argument(
        "--seconds", type=int, default=30,
        help="Capture duration in seconds (default: 30)",
    )
    parser.add_argument(
        "--max-events", type=int, default=5000,
        help="Max events to capture (default: 5000, API max: 10000)",
    )
    parser.add_argument(
        "--level", type=int, default=3, choices=[0, 1, 2, 3],
        help="Capture stage (default: 3 = before destination)",
    )
    parser.add_argument(
        "--appid-field", default="appId",
        help="Dot-separated field path for appId (default: 'appId')",
    )
    parser.add_argument(
        "--match-mode", default="exact",
        choices=["exact", "contains", "partition"],
        help="How to match appId to destination container (default: exact)",
    )
    parser.add_argument(
        "--output", default="appids_without_destination.csv",
        help="Output CSV path (default: appids_without_destination.csv)",
    )
    parser.add_argument(
        "--append", action="store_true",
        help="Append to CSV instead of overwriting (deduplicates automatically)",
    )
    parser.add_argument(
        "--inspect", action="store_true",
        help="Inspection mode: show destinations, capture sample, suggest filter",
    )
    parser.add_argument(
        "--no-verify-ssl", action="store_true",
        help="Disable SSL certificate verification",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    cribl_url = os.environ.get("CRIBL_URL", "").strip()
    if not cribl_url:
        sys.exit(
            "ERROR: Set CRIBL_URL (e.g. https://main-<org>.cribl.cloud "
            "or https://leader:9000)"
        )

    session = _build_session(verify_ssl=not args.no_verify_ssl)
    auth = CriblAuth(cribl_url, session)
    client = CriblClient(cribl_url, args.group, auth, session)

    try:
        if args.inspect:
            run_inspect(client, args.appid_field, args.level)
        else:
            run_analysis(client, args)
    except AuthenticationError as exc:
        log.error("%s", exc)
        sys.exit(1)
    except CriblAPIError as exc:
        log.error("Cribl API error: %s", exc)
        sys.exit(1)
    except requests.ConnectionError as exc:
        log.error("Connection failed: %s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
