"""AppId-to-destination matching engine and route/destination audit."""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("cribl_audit")


# ------------------------------------------------------------------
# Field extraction helpers
# ------------------------------------------------------------------

def get_nested(obj: Any, path: str) -> Any:
    """Walk a dot-separated *path* into a nested dict."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def extract_appid(event: dict[str, Any], field_path: str) -> str | None:
    """Extract an appId — tries top-level fields, then parses ``_raw``."""
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


# ------------------------------------------------------------------
# Destination matching
# ------------------------------------------------------------------

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


# ------------------------------------------------------------------
# Route / destination audit for lookup appIds
# ------------------------------------------------------------------

def check_lookup_route_dest_status(
    lookup_appids_hitting_default: set[str],
    destinations: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    match_mode: str,
) -> list[dict[str, Any]]:
    """Check whether lookup appIds also have routes and destinations in Cribl.

    Returns a list of dicts with status for each lookup appId:
      ``{apmId, has_destination, destination_id, has_route, route_id,
         route_output, status}``
    """
    results = []
    for app_id in sorted(lookup_appids_hitting_default):
        app_lower = app_id.lower()

        # --- destination check ---
        dest_id = match_appid_to_dest(app_id, destinations, match_mode)
        if dest_id is None:
            for dest in destinations:
                did = (dest.get("id") or "").lower()
                dname = (dest.get("name") or "").lower()
                if app_lower in did or app_lower in dname:
                    dest_id = dest.get("id", "?")
                    break

        # --- route check ---
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
