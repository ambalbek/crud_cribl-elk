"""Custom exception types."""

from __future__ import annotations

import json

import requests


class CriblAPIError(Exception):
    """Raised on non-2xx responses from the Cribl REST API."""

    def __init__(self, response: requests.Response) -> None:
        self.status_code = response.status_code
        self.url = response.url
        try:
            body = response.json()
            self.detail = (
                body.get("message") or body.get("error") or json.dumps(body)
            )
        except (ValueError, KeyError):
            self.detail = response.text[:500] if response.text else "(empty body)"
        super().__init__(
            f"HTTP {self.status_code} from {self.url}: {self.detail}"
        )


class AuthenticationError(Exception):
    """Raised when no valid credentials are available."""
