"""
AsyncCriblClient — async httpx-based client for Cribl Stream API.

This is the async counterpart to deps.py (which uses synchronous requests).
Used by the new semantic routers: stream.py, edge.py, workgroups.py.

Auth:
  - Prefer CRIBL_TOKEN (static bearer token).
  - Fall back to CRIBL_USERNAME + CRIBL_PASSWORD via /api/v1/auth/login.

All HTTP errors are re-raised as FastAPI HTTPException with the upstream
status code and a truncated body so callers get useful diagnostics.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

from .config import settings

logger = logging.getLogger("cribl-service")

_JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _raise_for(r: httpx.Response, context: str = "") -> Any:
    """Raise HTTPException if status >= 400; otherwise return parsed JSON."""
    if r.status_code >= 400:
        label = f"{context}: " if context else ""
        raise HTTPException(
            status_code=r.status_code,
            detail=f"{label}{r.text[:400]}",
        )
    return r.json() if r.content else {}


def _snapshot_dir(table: str) -> Path:
    """Return (and create) the snapshot directory for a given routes table."""
    d = Path(settings.snapshot_dir) / table
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_snapshot(table: str, payload: dict[str, Any]) -> Path:
    """Save a JSON snapshot of the route table before patching."""
    d = _snapshot_dir(table)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = d / f"routes_snapshot_{ts}.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


# ── Async client class ────────────────────────────────────────────────────────


class AsyncCriblClient:
    """
    Async HTTP client for Cribl Stream API.

    Instantiate via the get_async_cribl_client() FastAPI dependency.
    Do NOT create directly — auth is performed in the dependency factory.
    """

    def __init__(self, http: httpx.AsyncClient, bearer_token: str) -> None:
        self._http = http
        self._base = settings.base_url.rstrip("/")
        self._headers = {
            **_JSON_HEADERS,
            "Authorization": f"Bearer {bearer_token}",
        }

    # ── Low-level helpers ──────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return self._base + path

    async def _get(self, path: str, **kwargs: Any) -> Any:
        try:
            r = await self._http.get(
                self._url(path), headers=self._headers,
                timeout=settings.timeout, **kwargs,
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Cribl GET {path}: {exc}")
        return _raise_for(r, f"GET {path}")

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            r = await self._http.post(
                self._url(path), headers=self._headers,
                json=payload, timeout=settings.timeout,
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Cribl POST {path}: {exc}")
        return _raise_for(r, f"POST {path}")

    async def _patch(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            r = await self._http.patch(
                self._url(path), headers=self._headers,
                json=payload, timeout=settings.timeout,
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Cribl PATCH {path}: {exc}")
        return _raise_for(r, f"PATCH {path}")

    async def _put(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            r = await self._http.put(
                self._url(path), headers=self._headers,
                json=payload, timeout=settings.timeout,
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Cribl PUT {path}: {exc}")
        return _raise_for(r, f"PUT {path}")

    async def _delete(self, path: str) -> Any:
        try:
            r = await self._http.delete(
                self._url(path), headers=self._headers,
                timeout=settings.timeout,
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Cribl DELETE {path}: {exc}")
        return _raise_for(r, f"DELETE {path}")

    # ── Routes ────────────────────────────────────────────────────────────────

    async def list_routes(
        self, worker_group: str, table: str = "default"
    ) -> dict[str, Any]:
        """GET /api/v1/m/{worker_group}/routes/{table}."""
        return await self._get(f"/api/v1/m/{worker_group}/routes/{table}")

    async def get_route_by_app(
        self, worker_group: str, table: str, app_id: str
    ) -> dict[str, Any] | None:
        """
        Return the first route whose id or name matches app_id, or None if not found.
        Scans the full route table (no per-route GET endpoint in Cribl API).
        """
        data = await self.list_routes(worker_group, table)
        root = data.get("items", [data]) if isinstance(data.get("items"), list) else [data]
        # Try inner unwrap
        if isinstance(data, dict):
            obj = data
            if isinstance(obj.get("items"), list) and obj["items"]:
                inner = obj["items"][0]
                if isinstance(inner, dict) and "routes" in inner:
                    obj = inner
            routes = obj.get("routes", [])
            if not routes and isinstance(obj.get("items"), list):
                routes = obj["items"]
        else:
            routes = []
        app_lower = app_id.lower()
        for r in routes:
            if not isinstance(r, dict):
                continue
            if (str(r.get("id", "")).lower() == app_lower
                    or str(r.get("name", "")).lower() == app_lower):
                return r
        return None

    async def upsert_route(
        self,
        worker_group: str,
        table: str,
        route: dict[str, Any],
        group_id: str = "",
        create_group: bool = False,
        group_name: str = "",
        fallback_pipeline: str = "main",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Idempotent route upsert above the first final:true route.

        Steps (mirrors cribl-pusher.py logic):
        1. GET current route table
        2. Check for existing route by name/filter (skip if duplicate)
        3. Validate safety: existing_count >= min_existing_routes
        4. Insert new route above catch-all
        5. Save rollback snapshot (unless dry_run)
        6. PATCH route table back
        7. Return result dict with diff summary
        """
        import copy

        current = await self.list_routes(worker_group, table)

        # Unwrap Cribl {items: [{routes: [...]}]} response shape
        obj = current
        if isinstance(obj.get("items"), list) and obj["items"]:
            inner = obj["items"][0]
            if isinstance(inner, dict) and any(k in inner for k in ("routes", "groups")):
                obj = inner

        # Resolve target container
        target_routes: list[dict] | None = None
        if group_id:
            for attr in ("groups", "routeGroups"):
                groups_list = obj.get(attr, [])
                if isinstance(groups_list, list):
                    for g in groups_list:
                        if isinstance(g, dict) and str(g.get("id", "")) == group_id:
                            if not isinstance(g.get("routes"), list):
                                g["routes"] = []
                            target_routes = g["routes"]
                            break
                if target_routes is not None:
                    break
            if target_routes is None:
                if create_group:
                    groups_list = obj.setdefault("groups", [])
                    groups_list.append({"id": group_id, "name": group_name or group_id, "routes": []})
                    target_routes = groups_list[-1]["routes"]
                else:
                    raise HTTPException(status_code=404, detail=f"Route group '{group_id}' not found")
        elif isinstance(obj.get("routes"), list):
            target_routes = obj["routes"]
        else:
            raise HTTPException(status_code=502, detail=f"Cannot locate routes array in Cribl response. Keys={list(obj.keys())}")

        # Normalise route
        route = dict(route)
        if not route.get("name"):
            route["name"] = route.get("id", f"route_{worker_group}")
        route.setdefault("pipeline", fallback_pipeline)
        route.setdefault("final", False)
        route.setdefault("disabled", False)
        route.setdefault("clones", [])
        route.setdefault("description", "")
        route.setdefault("enableOutputExpression", False)

        # Idempotency check
        existing_names = {r.get("name") for r in target_routes if isinstance(r, dict)}
        existing_filters = {r.get("filter") for r in target_routes if isinstance(r, dict)}
        if route.get("name") in existing_names or route.get("filter") in existing_filters:
            return {
                "worker_group": worker_group, "table": table, "dry_run": dry_run,
                "status": "skipped", "reason": "route already exists",
                "route_name": route.get("name"),
            }

        # Safety check
        total = len(target_routes)
        if total < settings.min_existing_routes:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Safety check failed: only {total} existing routes, "
                    f"minimum is {settings.min_existing_routes}. "
                    "Use CRIBL_MIN_EXISTING_ROUTES=0 to override."
                ),
            )

        # Find insert position (above first final:true route)
        insert_at = len(target_routes)
        for i, r in enumerate(target_routes):
            if isinstance(r, dict) and r.get("final") is True:
                insert_at = i
                break
        else:
            for i, r in enumerate(target_routes):
                if not isinstance(r, dict):
                    continue
                rid = str(r.get("id", "") or r.get("name", "")).lower()
                if "default" in rid:
                    insert_at = i
                    break

        patch_obj = copy.deepcopy(obj)
        if group_id:
            for attr in ("groups", "routeGroups"):
                for g in patch_obj.get(attr, []):
                    if isinstance(g, dict) and str(g.get("id", "")) == group_id:
                        g.setdefault("routes", []).insert(insert_at, route)
        else:
            patch_obj.setdefault("routes", []).insert(insert_at, route)

        result = {
            "worker_group": worker_group, "table": table, "dry_run": dry_run,
            "status": "created", "route_name": route.get("name"),
            "insert_at": insert_at, "total_after": total + 1,
        }

        if dry_run:
            result["status"] = "dry_run"
            logger.info("upsert_route DRY-RUN wg=%s table=%s route=%s", worker_group, table, route.get("name"))
            return result

        snap = _save_snapshot(table, obj)
        result["snapshot"] = str(snap)
        logger.info("upsert_route wg=%s table=%s route=%s snapshot=%s", worker_group, table, route.get("name"), snap)
        await self._patch(f"/api/v1/m/{worker_group}/routes/{table}", patch_obj)
        return result

    async def update_route(
        self, worker_group: str, table: str, app_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Update fields on an existing route then PATCH the table back."""
        import copy
        current = await self.list_routes(worker_group, table)
        obj = current
        if isinstance(obj.get("items"), list) and obj["items"]:
            inner = obj["items"][0]
            if isinstance(inner, dict) and "routes" in inner:
                obj = inner
        routes = obj.get("routes", [])
        app_lower = app_id.lower()
        found = False
        for r in routes:
            if not isinstance(r, dict):
                continue
            if (str(r.get("id", "")).lower() == app_lower
                    or str(r.get("name", "")).lower() == app_lower):
                r.update({k: v for k, v in updates.items() if v is not None})
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail=f"Route '{app_id}' not found in table '{table}'")
        snap = _save_snapshot(table, current)
        logger.info("update_route wg=%s table=%s app_id=%s snapshot=%s", worker_group, table, app_id, snap)
        return await self._patch(f"/api/v1/m/{worker_group}/routes/{table}", obj)

    async def delete_route(
        self, worker_group: str, table: str, app_id: str, dry_run: bool = False
    ) -> dict[str, Any]:
        """Remove a route by id/name, then PATCH the table back."""
        import copy
        current = await self.list_routes(worker_group, table)
        obj = current
        if isinstance(obj.get("items"), list) and obj["items"]:
            inner = obj["items"][0]
            if isinstance(inner, dict) and "routes" in inner:
                obj = inner
        routes = obj.get("routes", [])
        app_lower = app_id.lower()
        before = len(routes)
        obj["routes"] = [
            r for r in routes
            if not (isinstance(r, dict) and (
                str(r.get("id", "")).lower() == app_lower
                or str(r.get("name", "")).lower() == app_lower
            ))
        ]
        if len(obj["routes"]) == before:
            raise HTTPException(status_code=404, detail=f"Route '{app_id}' not found in table '{table}'")
        result = {
            "worker_group": worker_group, "table": table, "app_id": app_id,
            "dry_run": dry_run, "removed": before - len(obj["routes"]),
        }
        if dry_run:
            return result
        snap = _save_snapshot(table, current)
        result["snapshot"] = str(snap)
        logger.info("delete_route wg=%s table=%s app_id=%s", worker_group, table, app_id)
        await self._patch(f"/api/v1/m/{worker_group}/routes/{table}", obj)
        return result

    # ── Destinations (outputs) ────────────────────────────────────────────────

    async def list_destinations(self, worker_group: str) -> list[dict]:
        data = await self._get(f"/api/v1/m/{worker_group}/system/outputs")
        return data.get("items", data) if isinstance(data, dict) else data

    async def get_destination(self, worker_group: str, dest_id: str) -> dict[str, Any]:
        return await self._get(f"/api/v1/m/{worker_group}/system/outputs/{dest_id}")

    async def create_destination(self, worker_group: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(f"/api/v1/m/{worker_group}/system/outputs", payload)

    async def update_destination(
        self, worker_group: str, dest_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._patch(f"/api/v1/m/{worker_group}/system/outputs/{dest_id}", payload)

    async def delete_destination(self, worker_group: str, dest_id: str) -> dict[str, Any]:
        return await self._delete(f"/api/v1/m/{worker_group}/system/outputs/{dest_id}")

    # ── Edge Fleets ────────────────────────────────────────────────────────────

    async def list_fleets(self) -> list[dict]:
        data = await self._get("/api/v1/fleet")
        return data.get("items", data) if isinstance(data, dict) else data

    async def get_fleet(self, fleet_id: str) -> dict[str, Any]:
        return await self._get(f"/api/v1/fleet/{fleet_id}")

    async def create_fleet(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/api/v1/fleet", payload)

    async def update_fleet(self, fleet_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/api/v1/fleet/{fleet_id}", payload)

    async def delete_fleet(self, fleet_id: str) -> dict[str, Any]:
        return await self._delete(f"/api/v1/fleet/{fleet_id}")

    # ── Worker Groups (leader-level) ──────────────────────────────────────────

    async def list_workgroups(self) -> list[dict]:
        data = await self._get("/api/v1/master/groups")
        return data.get("items", data) if isinstance(data, dict) else data

    async def get_workgroup(self, wg_id: str) -> dict[str, Any]:
        return await self._get(f"/api/v1/master/groups/{wg_id}")

    async def create_workgroup(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/api/v1/master/groups", payload)

    async def update_workgroup(self, wg_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._patch(f"/api/v1/master/groups/{wg_id}", payload)

    async def delete_workgroup(self, wg_id: str) -> dict[str, Any]:
        return await self._delete(f"/api/v1/master/groups/{wg_id}")


# ── FastAPI dependency ─────────────────────────────────────────────────────────


async def get_async_cribl_client() -> AsyncGenerator[AsyncCriblClient, None]:
    """
    FastAPI dependency that yields an authenticated AsyncCriblClient.
    Opens a single httpx.AsyncClient for the request lifetime, authenticates,
    then closes the client when the response is complete.
    """
    if not settings.base_url:
        raise HTTPException(status_code=500, detail="CRIBL_BASE_URL is not configured")

    ssl_verify = not settings.skip_ssl
    async with httpx.AsyncClient(verify=ssl_verify) as http:
        if settings.token:
            bearer = settings.token
        elif settings.username:
            try:
                r = await http.post(
                    f"{settings.base_url.rstrip('/')}/api/v1/auth/login",
                    headers=_JSON_HEADERS,
                    json={"username": settings.username, "password": settings.password},
                    timeout=settings.timeout,
                )
            except httpx.RequestError as exc:
                raise HTTPException(status_code=502, detail=f"Cribl login request error: {exc}")
            if r.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Cribl login failed: {r.status_code} {r.text[:300]}",
                )
            bearer = r.json().get("token", "")
            if not bearer:
                raise HTTPException(status_code=502, detail="Cribl login response missing token")
        else:
            raise HTTPException(
                status_code=500,
                detail="No Cribl credentials: set CRIBL_TOKEN or CRIBL_USERNAME + CRIBL_PASSWORD",
            )

        yield AsyncCriblClient(http, bearer)
