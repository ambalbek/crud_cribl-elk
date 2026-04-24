"""
CriblClient — authenticated HTTP client for Cribl Stream API.

Re-implements the three cribl_api.py functions that call die() (sys.exit) as
methods that raise HTTPException instead, so they are safe inside a FastAPI worker.

Pure-logic functions from cribl_api.py that have no side-effects are imported
directly: normalize_route, find_default_route_index, count_all_routes, unwrap_response.
"""
import logging
import os
import sys
from typing import Any

import requests
import urllib3
from fastapi import HTTPException

# Make project root importable so shared modules (cribl_api, cribl_utils, etc.)
# resolve correctly whether running via `uvicorn cribl_service.main:app` from /app
# or directly from the cribl_service/ directory during local development.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Safe pure-logic imports — none of these call die() or sys.exit()
from cribl_api import (  # noqa: E402
    normalize_route,
    find_default_route_index,
    count_all_routes,
    unwrap_response,
)

from .settings import (  # noqa: E402
    CRIBL_BASE_URL,
    CRIBL_TOKEN,
    CRIBL_USERNAME,
    CRIBL_PASSWORD,
    CRIBL_SKIP_SSL,
)

logger = logging.getLogger("cribl-service")

_JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}


class CriblClient:
    """
    One instance per request (created by get_cribl_client dependency).
    Authenticates on construction and exposes typed methods for each resource.
    """

    def __init__(self) -> None:
        if not CRIBL_BASE_URL:
            raise HTTPException(
                status_code=500,
                detail="CRIBL_BASE_URL env var is not set",
            )
        self._base = CRIBL_BASE_URL
        self._session = self._build_session()
        token = CRIBL_TOKEN or self._login()
        self._session.headers.update({
            **_JSON_HEADERS,
            "Authorization": f"Bearer {token}",
        })

    # ── Session / auth ──────────────────────────────────────────────────────

    def _build_session(self) -> requests.Session:
        if CRIBL_SKIP_SSL:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        s = requests.Session()
        s.verify = not CRIBL_SKIP_SSL
        s.proxies = {"http": "", "https": ""}
        return s

    def _login(self) -> str:
        """
        Re-implementation of cribl_api.cribl_login_token() that raises HTTPException
        instead of calling die() / sys.exit().
        """
        if not CRIBL_USERNAME or not CRIBL_PASSWORD:
            raise HTTPException(
                status_code=500,
                detail="No Cribl credentials: set CRIBL_TOKEN or CRIBL_USERNAME + CRIBL_PASSWORD",
            )
        url = f"{self._base}/api/v1/auth/login"
        try:
            r = self._session.post(url, headers=_JSON_HEADERS,
                                   json={"username": CRIBL_USERNAME, "password": CRIBL_PASSWORD},
                                   timeout=60)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Cribl login request error: {exc}")
        if r.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Cribl login failed: {r.status_code} {r.text[:300]}",
            )
        token = r.json().get("token")
        if not token:
            raise HTTPException(status_code=502, detail="Cribl login response missing token")
        return token

    # ── Low-level HTTP helpers ──────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return self._base + path

    def _raise(self, r: requests.Response) -> dict[str, Any]:
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text[:400])
        return r.json() if r.content else {}

    def _get(self, path: str) -> dict[str, Any]:
        try:
            r = self._session.get(self._url(path), timeout=60)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Cribl GET {path} error: {exc}")
        return self._raise(r)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._session.post(self._url(path), json=payload, timeout=60)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Cribl POST {path} error: {exc}")
        return self._raise(r)

    def _patch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._session.patch(self._url(path), json=payload, timeout=60)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Cribl PATCH {path} error: {exc}")
        return self._raise(r)

    def _delete(self, path: str) -> dict[str, Any]:
        try:
            r = self._session.delete(self._url(path), timeout=60)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Cribl DELETE {path} error: {exc}")
        return self._raise(r)

    # ── Route-table manipulation helpers ───────────────────────────────────

    def resolve_routes_target(
        self, obj: dict[str, Any], group_id: str | None
    ) -> tuple[dict, str]:
        """
        Re-implementation of cribl_api.get_routes_target() without die().
        Returns (target_container, routes_key) or raises HTTPException.
        """
        root = unwrap_response(obj)
        if group_id:
            for attr in ("groups", "routeGroups", "routesGroups"):
                groups = root.get(attr)
                if isinstance(groups, list):
                    for g in groups:
                        if isinstance(g, dict) and str(g.get("id", "")) == group_id:
                            if not isinstance(g.get("routes"), list):
                                g["routes"] = []
                            return g, "routes"
                    raise HTTPException(
                        status_code=404, detail=f"Route group '{group_id}' not found"
                    )
            raise HTTPException(status_code=404, detail="No groups array in Cribl response")
        if isinstance(root.get("routes"), list):
            return root, "routes"
        # items-as-routes shape
        if isinstance(obj.get("items"), list) and obj["items"]:
            item0 = obj["items"][0]
            if isinstance(item0, dict) and any(
                k in item0
                for k in ("filter", "pipeline", "output", "final", "disabled", "name", "id")
            ):
                return obj, "items"
        raise HTTPException(
            status_code=502,
            detail=f"Cannot locate routes array in Cribl response. Keys: {list(obj.keys())}",
        )

    def ensure_group(
        self, obj: dict[str, Any], group_id: str, group_name: str | None = None
    ) -> None:
        """
        Re-implementation of cribl_api.create_group_if_missing() without die().
        Mutates obj in-place.
        """
        root = unwrap_response(obj)
        groups = root.setdefault("groups", [])
        if not isinstance(groups, list):
            raise HTTPException(status_code=502, detail="groups field is not a list")
        for g in groups:
            if isinstance(g, dict) and str(g.get("id", "")) == group_id:
                if not isinstance(g.get("routes"), list):
                    g["routes"] = []
                return
        groups.append({"id": group_id, "name": group_name or group_id, "routes": []})

    # ── Routes resource ─────────────────────────────────────────────────────

    def get_routes(self, worker_group: str, table: str = "default") -> dict[str, Any]:
        return self._get(f"/api/v1/m/{worker_group}/routes/{table}")

    def patch_routes(
        self, worker_group: str, table: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._patch(f"/api/v1/m/{worker_group}/routes/{table}", payload)

    def add_route(
        self,
        worker_group: str,
        table: str,
        route: dict[str, Any],
        group_id: str = "",
        create_group: bool = False,
        group_name: str = "",
        fallback_pipeline: str = "main",
    ) -> dict[str, Any]:
        """
        Fetch the current route table, insert the new route above the first
        final:true route (using cribl_api logic), then PATCH the table back.
        """
        current = self.get_routes(worker_group, table)
        obj = unwrap_response(current)

        if group_id and create_group:
            self.ensure_group(obj, group_id, group_name or None)

        target, key = self.resolve_routes_target(obj, group_id or None)
        normalised = normalize_route(route, fallback_pipeline)
        insert_at = find_default_route_index(target[key])
        target[key].insert(insert_at, normalised)

        logger.info(
            "add_route worker_group=%s table=%s route_name=%s insert_at=%d",
            worker_group, table, normalised.get("name"), insert_at,
        )
        return self.patch_routes(worker_group, table, obj)

    def delete_route(
        self, worker_group: str, table: str, route_name: str, group_id: str = ""
    ) -> dict[str, Any]:
        """Remove a route by name or id, then PATCH the table back."""
        current = self.get_routes(worker_group, table)
        obj = unwrap_response(current)
        target, key = self.resolve_routes_target(obj, group_id or None)

        before = len(target[key])
        target[key] = [
            r for r in target[key]
            if r.get("id") != route_name and r.get("name") != route_name
        ]
        if len(target[key]) == before:
            raise HTTPException(status_code=404, detail=f"Route '{route_name}' not found")

        logger.info("delete_route worker_group=%s table=%s route_name=%s", worker_group, table, route_name)
        return self.patch_routes(worker_group, table, obj)

    # ── Provision (multi-app route + destination push) ─────────────────────

    def provision_apps(
        self,
        worker_group: str,
        apps: list[dict[str, str]],
        route_template: dict[str, Any],
        dest_template: dict[str, Any],
        dest_prefix: str,
        routes_table: str = "default",
        dry_run: bool = False,
        fallback_pipeline: str = "main",
    ) -> dict[str, Any]:
        """
        Idempotent bulk provision: add a route + destination for each app.

        Duplicates are detected by route name and destination id and skipped.
        All new routes are inserted in a single PATCH (unless dry_run).

        apps items: {"apmid": "...", "app_name": "..."}
        """
        import copy

        current = self.get_routes(worker_group, routes_table)
        obj = unwrap_response(current)
        target, key = self.resolve_routes_target(obj, None)

        existing_routes   = [r for r in target[key] if isinstance(r, dict)]
        existing_names    = {r.get("name")   for r in existing_routes if r.get("name")}
        existing_filters  = {r.get("filter") for r in existing_routes if r.get("filter")}
        default_idx       = find_default_route_index(existing_routes)

        outputs_data      = self._get(f"/api/v1/m/{worker_group}/system/outputs")
        existing_dest_ids = {
            item["id"]
            for item in outputs_data.get("items", [])
            if isinstance(item, dict) and item.get("id")
        }

        new_routes: list[dict] = []
        new_dests:  list[tuple[str, dict]] = []
        skipped_routes: list[str] = []
        skipped_dests:  list[str] = []

        for app in apps:
            apmid    = app["apmid"]
            app_name = app.get("app_name", apmid)

            route = copy.deepcopy(route_template)
            route["id"]     = apmid
            route["filter"] = f'apmId == "{apmid}"'
            route["output"] = f"{dest_prefix}-{apmid}"
            route["name"]   = f"{dest_prefix}-route-{apmid}"
            route = normalize_route(route, fallback_pipeline)

            if route["name"] in existing_names or route["filter"] in existing_filters:
                skipped_routes.append(apmid)
            else:
                new_routes.append(route)
                existing_names.add(route["name"])
                existing_filters.add(route["filter"])

            dest_id = f"{dest_prefix}-{apmid}"
            if dest_id in existing_dest_ids:
                skipped_dests.append(dest_id)
            else:
                dest = copy.deepcopy(dest_template)
                dest["id"]            = dest_id
                dest["containerName"] = apmid
                dest["description"]   = app_name
                if "name" in dest:
                    dest["name"] = dest_id
                new_dests.append((dest_id, dest))

        result: dict[str, Any] = {
            "worker_group":    worker_group,
            "routes_table":    routes_table,
            "dry_run":         dry_run,
            "new_routes":      [r["name"] for r in new_routes],
            "new_destinations": [d[0] for d in new_dests],
            "skipped_routes":  skipped_routes,
            "skipped_dests":   skipped_dests,
        }

        if dry_run:
            return result

        # Insert all new routes in one PATCH
        if new_routes:
            patch_obj = copy.deepcopy(obj)
            pt, pk = self.resolve_routes_target(patch_obj, None)
            pt[pk] = existing_routes[:default_idx] + new_routes + existing_routes[default_idx:]
            self.patch_routes(worker_group, routes_table, patch_obj)
            logger.info("provision_apps worker_group=%s patched %d route(s)", worker_group, len(new_routes))

        for dest_id, dest in new_dests:
            self._post(f"/api/v1/m/{worker_group}/system/outputs", dest)
            logger.info("provision_apps worker_group=%s created dest %s", worker_group, dest_id)

        return result

    # ── Destinations (outputs) resource ────────────────────────────────────

    def list_outputs(self, worker_group: str) -> list[dict]:
        data = self._get(f"/api/v1/m/{worker_group}/system/outputs")
        return data.get("items", data) if isinstance(data, dict) else data

    def get_output(self, worker_group: str, output_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/m/{worker_group}/system/outputs/{output_id}")

    def create_output(self, worker_group: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post(f"/api/v1/m/{worker_group}/system/outputs", payload)

    def update_output(
        self, worker_group: str, output_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._patch(f"/api/v1/m/{worker_group}/system/outputs/{output_id}", payload)

    def delete_output(self, worker_group: str, output_id: str) -> dict[str, Any]:
        return self._delete(f"/api/v1/m/{worker_group}/system/outputs/{output_id}")

    # ── Pipelines resource ──────────────────────────────────────────────────

    def list_pipelines(self, worker_group: str) -> list[dict]:
        data = self._get(f"/api/v1/m/{worker_group}/pipelines")
        return data.get("items", data) if isinstance(data, dict) else data

    def get_pipeline(self, worker_group: str, pipeline_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/m/{worker_group}/pipelines/{pipeline_id}")

    def create_pipeline(self, worker_group: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post(f"/api/v1/m/{worker_group}/pipelines", payload)

    def update_pipeline(
        self, worker_group: str, pipeline_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._patch(f"/api/v1/m/{worker_group}/pipelines/{pipeline_id}", payload)

    def delete_pipeline(self, worker_group: str, pipeline_id: str) -> dict[str, Any]:
        return self._delete(f"/api/v1/m/{worker_group}/pipelines/{pipeline_id}")

    # ── Worker groups (leader-level) ────────────────────────────────────────

    def list_worker_groups(self) -> list[dict]:
        data = self._get("/api/v1/master/groups")
        return data.get("items", data) if isinstance(data, dict) else data

    def get_worker_group(self, group_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/master/groups/{group_id}")

    def create_worker_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/v1/master/groups", payload)

    def update_worker_group(self, group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._patch(f"/api/v1/master/groups/{group_id}", payload)

    def delete_worker_group(self, group_id: str) -> dict[str, Any]:
        return self._delete(f"/api/v1/master/groups/{group_id}")

    # ── Leaders / system ────────────────────────────────────────────────────

    def get_system_info(self) -> dict[str, Any]:
        return self._get("/api/v1/system/info")

    def get_git_settings(self) -> dict[str, Any]:
        return self._get("/api/v1/system/settings/git-settings")

    def update_git_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._patch("/api/v1/system/settings/git-settings", payload)


def get_cribl_client() -> CriblClient:
    """FastAPI dependency — new authenticated client per request."""
    return CriblClient()
