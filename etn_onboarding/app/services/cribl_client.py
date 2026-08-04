"""
Synchronous Cribl Stream API client for the ETN Onboarding Flask app.

Adapted from the async ``cribl_service/cribl_client.py`` (httpx-based) but
uses the ``requests`` library so it can be called directly from Flask
request handlers without an async event loop.

All public methods are **stubs** that log the call and return a canned
response.  Each stub has a clean seam — replace the body with a real
``self._post`` / ``self._patch`` / ``self._get`` call when the upstream
Cribl endpoints are ready.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}


class CriblClient:
    """Synchronous REST client for the Cribl Stream API."""

    def __init__(self, base_url: str, token: str) -> None:
        """
        Parameters
        ----------
        base_url:
            Root URL of the Cribl Stream instance
            (e.g. ``https://cribl.example.com``).
        token:
            Bearer token used for authentication.
        """
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {**_JSON_HEADERS, "Authorization": f"Bearer {token}"}
        )

    # ── HTTP helpers ─────────────────────────────────────────────────────

    def _get(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        """Issue a GET request and return the parsed JSON response."""
        url = f"{self.base_url}{path}"
        resp = self._session.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _post(self, path: str, json: Any = None, **kwargs: Any) -> Dict[str, Any]:
        """Issue a POST request and return the parsed JSON response."""
        url = f"{self.base_url}{path}"
        resp = self._session.post(url, json=json, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _patch(self, path: str, json: Any = None, **kwargs: Any) -> Dict[str, Any]:
        """Issue a PATCH request and return the parsed JSON response."""
        url = f"{self.base_url}{path}"
        resp = self._session.patch(url, json=json, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _delete(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        """Issue a DELETE request and return the parsed JSON response."""
        url = f"{self.base_url}{path}"
        resp = self._session.delete(url, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # ── Stub operations ──────────────────────────────────────────────────
    # Each method below is a placeholder.  Replace the body with a real
    # API call (using the helpers above) once the endpoint contract is
    # finalised.  The return-type signatures are intentionally kept as
    # ``Dict`` so callers already handle dicts and the swap is seamless.

    def configure_edge_agent(
        self,
        app_name: str,
        apm_id: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Configure a Cribl Edge agent for the given application.

        **Stub** — logs the request and returns a canned success response.
        Replace with a real ``self._post`` call when the Edge provisioning
        API is available.

        Parameters
        ----------
        app_name:
            Human-readable application name.
        apm_id:
            Unique APM identifier for the application.
        config:
            Optional edge-agent configuration overrides.
        """
        logger.info(
            "configure_edge_agent called: app_name=%s apm_id=%s config=%s",
            app_name,
            apm_id,
            config,
        )
        return {"status": "configured", "app": app_name}

    def create_route(
        self,
        worker_group: str,
        table: str,
        route_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a Cribl route in the specified worker group and table.

        **Stub** — logs the request and returns a canned success response.
        Replace with ``self._post(f"/api/v1/m/{worker_group}/routes", ...)``
        when ready.

        Parameters
        ----------
        worker_group:
            Target Cribl worker-group identifier.
        table:
            Routing-table name (e.g. ``main``).
        route_payload:
            Full route definition matching the Cribl API schema.
        """
        logger.info(
            "create_route called: worker_group=%s table=%s payload=%s",
            worker_group,
            table,
            route_payload,
        )
        return {"status": "created", "worker_group": worker_group, "table": table}

    def create_destination(
        self,
        worker_group: str,
        dest_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a Cribl destination in the specified worker group.

        **Stub** — logs the request and returns a canned success response.
        Replace with ``self._post(f"/api/v1/m/{worker_group}/system/destinations", ...)``
        when ready.

        Parameters
        ----------
        worker_group:
            Target Cribl worker-group identifier.
        dest_payload:
            Full destination definition matching the Cribl API schema.
        """
        logger.info(
            "create_destination called: worker_group=%s payload=%s",
            worker_group,
            dest_payload,
        )
        return {"status": "created", "worker_group": worker_group}
