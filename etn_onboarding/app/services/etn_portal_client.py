"""
ETN Portal client for triggering Cribl configuration jobs.

This client communicates with the ETN Portal API to request automated
provisioning of Cribl Stream resources (routes, destinations, pipelines)
for a given application.  The portal processes these requests
asynchronously; use ``get_job_status`` to poll for completion.

All public methods are **stubs** that return canned responses.  Replace
each stub with a real HTTP call when the ETN Portal API is available.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}


class ETNPortalClient:
    """REST client for the ETN Portal Cribl-config provisioning API."""

    def __init__(self, base_url: str, api_key: Optional[str] = None) -> None:
        """
        Parameters
        ----------
        base_url:
            Root URL of the ETN Portal API
            (e.g. ``https://etn-portal.example.com/api``).
        api_key:
            Optional API key appended as a Bearer token.
        """
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(_JSON_HEADERS)
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"

    def trigger_cribl_config(
        self,
        apm_id: str,
        app_name: str,
        environment: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Submit a Cribl-configuration job to the ETN Portal.

        The portal will asynchronously create the required Cribl route,
        destination, and pipeline for the specified application and
        environment.

        **Stub** — logs the request and returns a synthetic job ID.
        Replace with a real ``POST`` to the portal's job-submission
        endpoint when the API contract is finalised.

        Parameters
        ----------
        apm_id:
            Unique APM identifier for the application.
        app_name:
            Human-readable application name.
        environment:
            Target environment (``dev``, ``stage``, ``prod``).
        config:
            Optional overrides forwarded to the portal.

        Returns
        -------
        dict
            ``{"job_id": "etn-<uuid>", "status": "submitted"}``
        """
        job_id = f"etn-{uuid.uuid4()}"
        logger.info(
            "trigger_cribl_config called: apm_id=%s app_name=%s env=%s "
            "config=%s -> job_id=%s",
            apm_id,
            app_name,
            environment,
            config,
            job_id,
        )
        return {"job_id": job_id, "status": "submitted"}

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Poll the ETN Portal for the status of a configuration job.

        **Stub** — always returns ``"complete"``.  Replace with a real
        ``GET`` to the portal's job-status endpoint.

        Parameters
        ----------
        job_id:
            Job identifier returned by ``trigger_cribl_config``.

        Returns
        -------
        dict
            ``{"job_id": "<job_id>", "status": "complete"}``
        """
        logger.info("get_job_status called: job_id=%s", job_id)
        return {"job_id": job_id, "status": "complete"}
