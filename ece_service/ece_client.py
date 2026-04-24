"""
AsyncECEClient — async httpx client for Elasticsearch and Kibana APIs.

This module provides an async counterpart to deps.py's sync ECEClient.
Use this client in new async FastAPI routers (e.g. ILM, index templates).
Existing sync routers continue to use deps.py / ECEClient.
"""
import base64
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from fastapi import HTTPException

from .config import settings

logger = logging.getLogger("ece-service")


def _build_auth_headers(token: str, username: str, password: str) -> dict[str, str]:
    base = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        return {**base, "Authorization": f"ApiKey {token}"}
    if username and password:
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {**base, "Authorization": f"Basic {creds}"}
    return base


def _raise_for(r: httpx.Response, context: str) -> dict[str, Any]:
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code,
                            detail=f"{context}: {r.text[:400]}")
    return r.json() if r.content else {}


class AsyncECEClient:
    """
    Async ES + Kibana client.  One instance lives for the lifetime of one
    request (injected via get_async_ece_client dependency).
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        if not settings.es_url:
            raise HTTPException(status_code=500, detail="ECE_ES_URL env var is not set")

        self._http = http
        self._es_base = settings.es_url.rstrip("/")
        self._es_prod_base = (settings.es_url_prod or settings.es_url).rstrip("/")
        self._kibana_base = settings.kibana_url.rstrip("/") if settings.kibana_url else ""

        self._es_headers = _build_auth_headers(
            settings.es_token, settings.es_username, settings.es_password
        )
        self._es_prod_headers = _build_auth_headers(
            settings.es_token_prod, settings.es_username_prod, settings.es_password_prod
        )
        self._kib_headers = {
            **_build_auth_headers(
                settings.kibana_token, settings.kibana_username, settings.kibana_password
            ),
            "kbn-xsrf": "true",
        }

    # ── Low-level helpers ─────────────────────────────────────────────────────

    def _es_url(self, path: str, prod: bool = False) -> str:
        return (self._es_prod_base if prod else self._es_base) + path

    def _es_hdrs(self, prod: bool = False) -> dict[str, str]:
        return self._es_prod_headers if prod else self._es_headers

    async def _es(self, method: str, path: str, prod: bool = False, **kw) -> dict[str, Any]:
        try:
            r = await self._http.request(
                method, self._es_url(path, prod), headers=self._es_hdrs(prod), **kw
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"ES {method} {path}: {exc}")
        return _raise_for(r, f"ES {method} {path}")

    async def _kibana(self, method: str, path: str, **kw) -> dict[str, Any]:
        if not self._kibana_base:
            raise HTTPException(status_code=500, detail="ECE_KIBANA_URL is not set")
        try:
            r = await self._http.request(
                method, self._kibana_base + path, headers=self._kib_headers, **kw
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Kibana {method} {path}: {exc}")
        return _raise_for(r, f"Kibana {method} {path}")

    # ── ILM policies ──────────────────────────────────────────────────────────

    async def list_ilm_policies(self, prod: bool = False) -> dict[str, Any]:
        return await self._es("GET", "/_ilm/policy", prod=prod)

    async def get_ilm_policy(self, name: str, prod: bool = False) -> dict[str, Any]:
        return await self._es("GET", f"/_ilm/policy/{name}", prod=prod)

    async def put_ilm_policy(
        self, name: str, body: dict[str, Any], prod: bool = False
    ) -> dict[str, Any]:
        return await self._es("PUT", f"/_ilm/policy/{name}", prod=prod, json=body)

    async def delete_ilm_policy(self, name: str, prod: bool = False) -> dict[str, Any]:
        return await self._es("DELETE", f"/_ilm/policy/{name}", prod=prod)

    async def explain_ilm(self, index: str, prod: bool = False) -> dict[str, Any]:
        return await self._es("GET", f"/{index}/_ilm/explain", prod=prod)

    # ── Index templates ───────────────────────────────────────────────────────

    async def list_index_templates(self, prod: bool = False) -> dict[str, Any]:
        return await self._es("GET", "/_index_template", prod=prod)

    async def get_index_template(self, name: str, prod: bool = False) -> dict[str, Any]:
        return await self._es("GET", f"/_index_template/{name}", prod=prod)

    async def put_index_template(
        self, name: str, body: dict[str, Any], prod: bool = False
    ) -> dict[str, Any]:
        return await self._es("PUT", f"/_index_template/{name}", prod=prod, json=body)

    async def delete_index_template(self, name: str, prod: bool = False) -> dict[str, Any]:
        return await self._es("DELETE", f"/_index_template/{name}", prod=prod)

    # ── ES Security roles (async) ─────────────────────────────────────────────

    async def list_roles(self, prod: bool = False) -> dict[str, Any]:
        return await self._es("GET", "/_security/role", prod=prod)

    async def get_role(self, name: str, prod: bool = False) -> dict[str, Any]:
        return await self._es("GET", f"/_security/role/{name}", prod=prod)

    async def put_role(
        self, name: str, body: dict[str, Any], prod: bool = False
    ) -> dict[str, Any]:
        return await self._es("PUT", f"/_security/role/{name}", prod=prod, json=body)

    async def delete_role(self, name: str, prod: bool = False) -> dict[str, Any]:
        return await self._es("DELETE", f"/_security/role/{name}", prod=prod)

    # ── ES Security role-mappings (async) ─────────────────────────────────────

    async def list_role_mappings(self, prod: bool = False) -> dict[str, Any]:
        return await self._es("GET", "/_security/role_mapping", prod=prod)

    async def get_role_mapping(self, name: str, prod: bool = False) -> dict[str, Any]:
        return await self._es("GET", f"/_security/role_mapping/{name}", prod=prod)

    async def put_role_mapping(
        self, name: str, body: dict[str, Any], prod: bool = False
    ) -> dict[str, Any]:
        return await self._es("PUT", f"/_security/role_mapping/{name}", prod=prod, json=body)

    async def delete_role_mapping(self, name: str, prod: bool = False) -> dict[str, Any]:
        return await self._es("DELETE", f"/_security/role_mapping/{name}", prod=prod)


async def get_async_ece_client() -> AsyncGenerator[AsyncECEClient, None]:
    """
    FastAPI async dependency — creates one httpx.AsyncClient per request,
    yields an AsyncECEClient, then closes the underlying connection pool.
    """
    ssl_verify = not settings.skip_ssl
    async with httpx.AsyncClient(
        verify=ssl_verify,
        timeout=settings.timeout,
    ) as http:
        yield AsyncECEClient(http)
