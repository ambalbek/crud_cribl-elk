"""
Harness CI/CD client for triggering Azure Blob storage provisioning.

This client communicates with the Harness platform API to kick off a
pipeline that creates an Azure Blob Storage container for a given
application.  The pipeline runs asynchronously; use
``get_pipeline_status`` to poll for completion.

All public methods are **stubs** that return canned responses.  Replace
each stub with a real HTTP call when the Harness API integration is
ready.
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


class HarnessClient:
    """REST client for the Harness pipeline-execution API."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> None:
        """
        Parameters
        ----------
        base_url:
            Root URL of the Harness API gateway
            (e.g. ``https://app.harness.io/gateway``).
        api_key:
            Optional Harness API key used as a Bearer token.
        account_id:
            Harness account identifier, required by most Harness
            endpoints.
        """
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self._session = requests.Session()
        self._session.headers.update(_JSON_HEADERS)
        if api_key:
            self._session.headers["x-api-key"] = api_key

    def trigger_blob_storage(
        self,
        apm_id: str,
        app_name: str,
        environment: str,
        storage_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Trigger a Harness pipeline to create Azure Blob storage.

        The pipeline will provision a new Azure Blob Storage container
        (and any required resource-group / storage-account scaffolding)
        for the specified application and environment.

        **Stub** — logs the request and returns a synthetic pipeline ID.
        Replace with a real ``POST`` to the Harness pipeline-execution
        endpoint when the integration is ready.

        Parameters
        ----------
        apm_id:
            Unique APM identifier for the application.
        app_name:
            Human-readable application name.
        environment:
            Target environment (``dev``, ``stage``, ``prod``).
        storage_config:
            Optional overrides (container name, retention, etc.).

        Returns
        -------
        dict
            ``{"pipeline_id": "harness-<uuid>", "status": "submitted"}``
        """
        pipeline_id = f"harness-{uuid.uuid4()}"
        logger.info(
            "trigger_blob_storage called: apm_id=%s app_name=%s env=%s "
            "storage_config=%s -> pipeline_id=%s",
            apm_id,
            app_name,
            environment,
            storage_config,
            pipeline_id,
        )
        return {"pipeline_id": pipeline_id, "status": "submitted"}

    def get_pipeline_status(self, pipeline_id: str) -> Dict[str, Any]:
        """Poll Harness for the execution status of a pipeline.

        **Stub** — always returns ``"success"``.  Replace with a real
        ``GET`` to the Harness pipeline-execution-details endpoint.

        Parameters
        ----------
        pipeline_id:
            Pipeline execution identifier returned by
            ``trigger_blob_storage``.

        Returns
        -------
        dict
            ``{"pipeline_id": "<pipeline_id>", "status": "success"}``
        """
        logger.info("get_pipeline_status called: pipeline_id=%s", pipeline_id)
        return {"pipeline_id": pipeline_id, "status": "success"}
