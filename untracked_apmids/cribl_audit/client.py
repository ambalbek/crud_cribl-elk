"""Read-only Cribl Stream REST API client."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from .auth import CriblAuth
from .constants import CAPTURE_READ_TIMEOUT_PAD, CONNECT_TIMEOUT, READ_TIMEOUT
from .http import raise_for_status

log = logging.getLogger("cribl_audit")


class CriblClient:
    """Thin wrapper around the Cribl Stream REST API (read-only)."""

    def __init__(
        self,
        cribl_url: str,
        group: str,
        auth: CriblAuth,
        session: requests.Session,
    ) -> None:
        self._base = f"{cribl_url.rstrip('/')}/api/v1/m/{group}"
        self._group = group
        self._auth = auth
        self._session = session

    @property
    def group(self) -> str:
        return self._group

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth.token}",
            "Content-Type": "application/json",
            "Accept": "application/x-ndjson, application/json",
        }

    # ------------------------------------------------------------------
    # Outputs
    # ------------------------------------------------------------------

    def list_outputs(self) -> list[dict[str, Any]]:
        """GET /system/outputs — all configured destinations."""
        url = f"{self._base}/system/outputs"
        log.debug("GET %s", url)
        resp = self._session.get(
            url, headers=self._headers(), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        raise_for_status(resp)
        data = self._parse_json(resp, url)
        return data.get("items", data) if isinstance(data, dict) else data

    def list_azure_blob_outputs(self) -> list[dict[str, Any]]:
        """Return only ``azure_blob`` type destinations."""
        return [o for o in self.list_outputs() if o.get("type") == "azure_blob"]

    def find_default_output_id(self) -> str | None:
        """Return the output ID marked as the default destination."""
        for o in self.list_outputs():
            if o.get("type") == "default":
                return o.get("defaultId")
        return None

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def list_routes(self) -> list[dict[str, Any]]:
        """GET /routes — all configured routes."""
        url = f"{self._base}/routes"
        log.debug("GET %s", url)
        resp = self._session.get(
            url, headers=self._headers(), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        raise_for_status(resp)
        data = self._parse_json(resp, url)
        if isinstance(data, dict):
            routes = data.get("items") or data.get("routes") or []
            if not routes and "groups" in data:
                for g in data["groups"].values():
                    routes.extend(g.get("routes", []))
            return routes
        return data

    # ------------------------------------------------------------------
    # Live capture
    # ------------------------------------------------------------------

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
        raise_for_status(resp)

        events: list[dict[str, Any]] = []
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                log.debug("Skipping non-JSON line from capture stream")
        return events

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_json(self, resp: requests.Response, url: str) -> Any:
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError):
            body_preview = resp.text[:200] if resp.text else "(empty)"
            raise RuntimeError(
                f"Group '{self._group}': expected JSON from {url} "
                f"but got: {body_preview}\n"
                f"Check that the group name is correct and exists in Cribl."
            )
