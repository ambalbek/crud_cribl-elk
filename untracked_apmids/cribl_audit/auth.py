"""Thread-safe Cribl authentication — OAuth2, leader login, static token."""

from __future__ import annotations

import json
import logging
import os
import threading
import time

import requests

from .constants import CONNECT_TIMEOUT, CRIBL_CLOUD_AUDIENCE, CRIBL_CLOUD_LOGIN_URL, READ_TIMEOUT
from .exceptions import AuthenticationError
from .http import raise_for_status

log = logging.getLogger("cribl_audit")


class CriblAuth:
    """Thread-safe authentication manager with automatic token refresh."""

    def __init__(self, cribl_url: str, session: requests.Session) -> None:
        self._cribl_url = cribl_url.rstrip("/")
        self._session = session
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def token(self) -> str:
        """Return a valid bearer token, refreshing if necessary."""
        with self._lock:
            if self._token and time.time() < self._expires_at:
                return self._token
            self._authenticate()
            if not self._token:
                raise AuthenticationError("Authentication succeeded but no token was returned.")
            return self._token

    def ensure_fresh(self) -> None:
        """Force a token refresh if close to expiry (within 120 s)."""
        with self._lock:
            if not self._token or time.time() > self._expires_at - 120:
                log.info("Token expiring soon or missing — refreshing")
                self._authenticate()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _authenticate(self) -> None:
        client_id = os.environ.get("CRIBL_CLIENT_ID", "").strip()
        client_secret = os.environ.get("CRIBL_CLIENT_SECRET", "").strip()
        username = os.environ.get("CRIBL_USERNAME", "").strip()
        password = os.environ.get("CRIBL_PASSWORD", "").strip()
        static_token = os.environ.get("CRIBL_TOKEN", "").strip()

        if client_id and client_secret:
            self._cloud_oauth(client_id, client_secret)
        elif username and password:
            self._leader_login(username, password)
        elif static_token:
            self._token = static_token
            self._expires_at = time.time() + 86400
            log.info("Using static bearer token from CRIBL_TOKEN")
        else:
            raise AuthenticationError(
                "No Cribl credentials found. Set one of:\n"
                "  CRIBL_CLIENT_ID + CRIBL_CLIENT_SECRET  (Cribl Cloud)\n"
                "  CRIBL_USERNAME  + CRIBL_PASSWORD        (self-managed leader)\n"
                "  CRIBL_TOKEN                             (pre-existing bearer token)"
            )

    def _cloud_oauth(self, client_id: str, client_secret: str) -> None:
        log.info("Authenticating via Cribl Cloud OAuth2 (%s)", CRIBL_CLOUD_LOGIN_URL)
        resp = self._session.post(
            CRIBL_CLOUD_LOGIN_URL,
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "audience": CRIBL_CLOUD_AUDIENCE,
            },
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        raise_for_status(resp)
        data = resp.json()
        self._token = data["access_token"]
        self._expires_at = time.time() + data.get("expires_in", 3600) - 60
        log.info("Cloud OAuth token acquired (expires in %ds)", data.get("expires_in", 0))

    def _leader_login(self, username: str, password: str) -> None:
        url = f"{self._cribl_url}/api/v1/auth/login"
        log.info("Authenticating via leader login (%s)", url)
        resp = self._session.post(
            url,
            json={"username": username, "password": password},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        raise_for_status(resp)
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            body_preview = resp.text[:300] if resp.text else "(empty)"
            raise AuthenticationError(
                f"Login endpoint returned non-JSON response.\n"
                f"  URL: {url}\n"
                f"  Status: {resp.status_code}\n"
                f"  Body: {body_preview}\n"
                f"  Check that CRIBL_URL is correct (should be the base URL, "
                f"e.g. https://leader:9000 or https://main-org.cribl.cloud)"
            )
        self._token = data.get("token") or data.get("access_token")
        if not self._token:
            raise AuthenticationError(
                f"Login succeeded (HTTP {resp.status_code}) but no token in response.\n"
                f"  Keys returned: {list(data.keys())}"
            )
        self._expires_at = time.time() + 3600
        log.info("Leader login token acquired")
