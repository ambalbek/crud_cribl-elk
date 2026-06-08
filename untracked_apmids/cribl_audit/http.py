"""HTTP session factory with retry logic."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import CriblAPIError


def build_session(verify_ssl: bool = True) -> requests.Session:
    """Build a ``requests.Session`` with automatic retries on 429/5xx."""
    session = requests.Session()
    session.verify = verify_ssl
    if not verify_ssl:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def raise_for_status(resp: requests.Response) -> None:
    """Raise :class:`CriblAPIError` on any 4xx/5xx response."""
    if resp.status_code >= 400:
        raise CriblAPIError(resp)
