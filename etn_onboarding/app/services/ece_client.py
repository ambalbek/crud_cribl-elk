"""
Synchronous ECE (Elastic Cloud Enterprise) client for the ETN Onboarding Flask app.

Adapted from the async ``ece_service`` client but uses the ``requests``
library for synchronous Flask compatibility.

All public methods are **stubs** that log the call and return a canned
response.  Replace each stub body with a real HTTP call when the
Elasticsearch / Kibana endpoints are confirmed.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)

_JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}


class ECEClient:
    """Synchronous REST client for Elasticsearch / Kibana security APIs."""

    def __init__(self, es_url: str, auth_header: str) -> None:
        """
        Parameters
        ----------
        es_url:
            Base URL of the Elasticsearch cluster
            (e.g. ``https://es.example.com:9200``).
        auth_header:
            Value for the ``Authorization`` header — typically
            ``Basic <base64>`` or ``ApiKey <token>``.
        """
        self.es_url = es_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {**_JSON_HEADERS, "Authorization": auth_header}
        )

    # ── Stub operations ──────────────────────────────────────────────────
    # Each method below is a placeholder.  Replace the body with a real
    # HTTP call when the endpoint contract is finalised.

    def create_role(self, name: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Create an Elasticsearch security role.

        **Stub** — logs the request and returns a canned success response.
        Replace with a PUT to ``/_security/role/{name}`` when ready.

        Parameters
        ----------
        name:
            Role name to create.
        body:
            Role definition (cluster privileges, index patterns, etc.).
        """
        logger.info("create_role called: name=%s body=%s", name, body)
        return {"role": {"created": True, "name": name}}

    def create_role_mapping(self, name: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Create an Elasticsearch role mapping.

        **Stub** — logs the request and returns a canned success response.
        Replace with a PUT to ``/_security/role_mapping/{name}`` when ready.

        Parameters
        ----------
        name:
            Role-mapping name.
        body:
            Mapping definition (roles, rules, metadata).
        """
        logger.info("create_role_mapping called: name=%s body=%s", name, body)
        return {"role_mapping": {"created": True, "name": name}}

    def create_index(self, name: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Create an Elasticsearch index.

        **Stub** — logs the request and returns a canned success response.
        Replace with a PUT to ``/{name}`` when ready.

        Parameters
        ----------
        name:
            Index name to create.
        body:
            Index settings and mappings.
        """
        logger.info("create_index called: name=%s body=%s", name, body)
        return {"index": {"acknowledged": True, "index": name}}
