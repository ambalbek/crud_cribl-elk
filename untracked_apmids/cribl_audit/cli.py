"""Command-line interface and entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import textwrap
import time
from typing import Any

import requests

from .analysis import run_analysis, run_dry_run, run_inspect
from .auth import CriblAuth
from .client import CriblClient
from .config import load_config, load_env_file
from .constants import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_OK
from .exceptions import AuthenticationError, CriblAPIError
from .http import build_session


def _build_parser() -> argparse.ArgumentParser:
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
              python -m cribl_audit --config config.json --inspect

              # 2. Dry run — validate config without capturing events
              python -m cribl_audit --config config.json --dry-run

              # 3. Full analysis
              python -m cribl_audit --config config.json

            Exit codes:
              0   Success
              1   Fatal error (auth, connectivity, no events)
              2   Partial failure (some groups failed, results from others saved)
              130 Interrupted (Ctrl+C — partial results saved)
        """),
    )

    parser.add_argument("--config", default=None, metavar="PATH",
                        help="Path to JSON config file (see config.example.json).")
    parser.add_argument("--group", nargs="+", default=None,
                        help="Cribl worker group name(s) — multiple groups run in parallel")
    parser.add_argument("--filter", default=None,
                        help="JavaScript filter for capture (default: auto-detect)")
    parser.add_argument("--default-output", default=None, metavar="ID",
                        help="Default output ID (overrides auto-detection)")
    parser.add_argument("--seconds", type=int, default=None,
                        help="Capture duration in seconds (default: 30)")
    parser.add_argument("--max-events", type=int, default=None,
                        help="Max events to capture (default: 5000)")
    parser.add_argument("--level", type=int, default=None, choices=[0, 1, 2, 3],
                        help="Capture stage (default: 3 = before destination)")
    parser.add_argument("--appid-field", default=None,
                        help="Dot-separated field path for appId (default: apmId)")
    parser.add_argument("--match-mode", default=None, choices=["exact", "contains", "partition"],
                        help="How to match appId to destination container (default: exact)")
    parser.add_argument("--output", default=None,
                        help="Output CSV path (default: timestamped)")
    parser.add_argument("--append", action="store_true", default=None,
                        help="Append to CSV instead of overwriting")
    parser.add_argument("--rounds", type=int, default=None,
                        help="Number of capture rounds (default: 1)")
    parser.add_argument("--interval", type=int, default=None,
                        help="Seconds between capture rounds (default: 60)")
    parser.add_argument("--format", default=None, choices=["csv", "json", "both"],
                        help="Output format (default: csv)")
    parser.add_argument("--diff-csv", default=None, metavar="PATH",
                        help="Previous CSV to diff against")
    parser.add_argument("--lookup", default=None, metavar="PATH",
                        help="Lookup JSON file with known containers to exclude")
    parser.add_argument("--es-url", default=None, metavar="URL",
                        help="Elasticsearch URL")
    parser.add_argument("--es-index", default=None, metavar="NAME",
                        help="Elasticsearch index name")
    parser.add_argument("--inspect", action="store_true",
                        help="Discovery mode: show destinations, capture sample")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate config without capturing events")
    parser.add_argument("--env-file", default=None, metavar="PATH",
                        help="Path to .env file with credentials")
    parser.add_argument("--log-file", default=None, metavar="PATH",
                        help="Write log output to file")
    parser.add_argument("--no-verify-ssl", action="store_true", default=None,
                        help="Disable SSL certificate verification")
    parser.add_argument("-v", "--verbose", action="store_true", default=None,
                        help="Enable debug logging")
    return parser


def main() -> None:
    """CLI entry point."""
    # Pass 1: peek at --config to load defaults
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", default=None)
    pre_args, _ = pre_parser.parse_known_args()

    config_defaults: dict[str, Any] = {}
    if pre_args.config:
        config_defaults = load_config(pre_args.config)

    # Pass 2: full parse
    parser = _build_parser()
    args = parser.parse_args()

    # Merge: CLI > config.json > hardcoded defaults
    for key, value in config_defaults.items():
        if getattr(args, key, None) is None:
            setattr(args, key, value)

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

    if not args.group:
        parser.error("--group is required (via CLI or config file)")

    # Logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )

    if args.env_file:
        load_env_file(args.env_file)

    if args.default_output:
        os.environ["CRIBL_DEFAULT_OUTPUT_ID"] = args.default_output

    cribl_url = os.environ.get("CRIBL_URL", "").strip()
    if not cribl_url:
        sys.exit("ERROR: Set CRIBL_URL (via env, --config auth section, or --env-file)")

    session = build_session(verify_ssl=not args.no_verify_ssl)
    auth = CriblAuth(cribl_url, session)
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
        logging.getLogger("cribl_audit").error("%s", exc)
        sys.exit(EXIT_ERROR)
    except CriblAPIError as exc:
        logging.getLogger("cribl_audit").error("Cribl API error: %s", exc)
        sys.exit(EXIT_ERROR)
    except requests.ConnectionError as exc:
        logging.getLogger("cribl_audit").error("Connection failed: %s", exc)
        sys.exit(EXIT_ERROR)
    except KeyboardInterrupt:
        print("\nInterrupted — partial results saved if available.")
        sys.exit(EXIT_INTERRUPTED)
