"""Lookup table loading and CSV diff logic."""

from __future__ import annotations

import csv
import glob as globmod
import json
import logging
import os

log = logging.getLogger("cribl_audit")


def load_lookup_appids(path: str) -> set[str]:
    """Load known container names from a lookup JSON file.

    Reads ``azure_storage_account_containers`` and returns a lowered set.
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
    """Find the most recent ``appids_without_destination_*.csv`` other than *current_output*."""
    pattern = os.path.join(output_dir, "appids_without_destination_*.csv")
    candidates = sorted(globmod.glob(pattern), reverse=True)
    current_abs = os.path.abspath(current_output)
    for path in candidates:
        if os.path.abspath(path) != current_abs:
            return path
    return None
