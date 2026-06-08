"""Minimal Elasticsearch client — bulk-indexes appId results."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

from .constants import CONNECT_TIMEOUT, READ_TIMEOUT
from .http import build_session
from .output import ResultRow

log = logging.getLogger("cribl_audit")


class ElasticsearchClient:
    """Bulk-indexes unmatched appId documents into Elasticsearch."""

    def __init__(
        self,
        url: str,
        index: str,
        *,
        api_key: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self._url = url.strip().rstrip("/")
        self._index = index.strip().strip("/")
        self._session = build_session(verify_ssl=verify_ssl)
        self._session.headers.update({"Content-Type": "application/x-ndjson"})
        self._auth: tuple[str, str] | None = None

        if api_key:
            self._session.headers["Authorization"] = f"ApiKey {api_key}"
        elif username and password:
            self._auth = (username, password)

    def test_connection(self) -> tuple[bool, str]:
        """Quick connectivity check — GET / on the ES cluster."""
        try:
            resp = self._session.get(
                self._url, auth=self._auth,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            if resp.status_code == 401:
                return False, f"Authentication failed (HTTP 401): {resp.text[:300]}"
            if resp.status_code == 403:
                return False, f"Authorization denied (HTTP 403): {resp.text[:300]}"
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
            try:
                data = resp.json()
                version = data.get("version", {}).get("number", "unknown")
                name = data.get("name", "unknown")
                return True, f"Connected — cluster '{name}', ES {version}"
            except (json.JSONDecodeError, ValueError):
                return False, (
                    f"ES URL returned non-JSON (HTTP {resp.status_code}). "
                    f"Body: {resp.text[:300]}\n"
                    f"Check that {self._url} is the ES API, not Kibana or a load balancer."
                )
        except requests.RequestException as exc:
            return False, f"Connection failed: {exc}"

    def index_results(
        self,
        rows: list[ResultRow],
        *,
        group: str,
        total_events: int,
        is_new: bool = True,
    ) -> int:
        """Bulk-index appId results. Returns number of docs indexed."""
        if not rows:
            return 0
        if not self._index:
            log.error("ES index name is empty — cannot index")
            return 0

        bulk_url = f"{self._url}/{self._index}/_bulk"
        timestamp = datetime.now(timezone.utc).isoformat()
        safe_group = group.replace("\n", " ").replace("\r", " ")

        ndjson_lines: list[str] = []
        for apm_id, app_name, output_id, matched, count in rows:
            action = json.dumps({"index": {}})
            doc = json.dumps({
                "@timestamp": timestamp,
                "group": safe_group,
                "apmId": apm_id,
                "appName": app_name,
                "outputId": output_id,
                "matched_destination": matched,
                "is_unmatched": matched == "DEFAULT",
                "is_new": is_new,
                "event_count": count,
                "total_events_captured": total_events,
            })
            ndjson_lines.append(action)
            ndjson_lines.append(doc)

        body = "\n".join(ndjson_lines) + "\n"
        log.info("ES _bulk request: %d docs, %d bytes to %s", len(rows), len(body), bulk_url)

        try:
            resp = self._session.post(
                bulk_url, auth=self._auth,
                data=body.encode("utf-8"),
                timeout=(CONNECT_TIMEOUT, 60),
            )
        except requests.ConnectionError as exc:
            log.error("ES connection refused: %s\n  Check that ES_URL=%s is reachable.", exc, self._url)
            return 0
        except requests.Timeout:
            log.error("ES request timed out after 60s to %s", bulk_url)
            return 0
        except requests.RequestException as exc:
            log.error("ES request failed: %s", exc)
            return 0

        log.debug("ES response: HTTP %d, %d bytes, url=%s", resp.status_code, len(resp.text), resp.url)

        if resp.url and resp.url.rstrip("/") != bulk_url.rstrip("/"):
            log.warning(
                "ES request was redirected: %s -> %s. "
                "Check that ES_URL points to ES, not Kibana or a proxy.",
                bulk_url, resp.url,
            )

        if resp.status_code == 401:
            log.error(
                "ES authentication failed (HTTP 401).\n"
                "  URL: %s\n  Response: %s\n"
                "  Fix: set ES_API_KEY or ES_USERNAME+ES_PASSWORD in config/env.",
                bulk_url, resp.text[:500],
            )
            return 0
        if resp.status_code == 403:
            log.error(
                "ES authorization denied (HTTP 403).\n"
                "  URL: %s\n  Response: %s\n"
                "  Fix: ensure the ES user/key has write access to index '%s'.",
                bulk_url, resp.text[:500], self._index,
            )
            return 0
        if resp.status_code >= 400:
            log.error(
                "ES bulk index failed: HTTP %d\n  URL: %s\n  Response: %s",
                resp.status_code, bulk_url, resp.text[:2000],
            )
            return 0

        try:
            result = resp.json()
        except (json.JSONDecodeError, ValueError):
            log.error(
                "ES returned non-JSON (HTTP %d).\n  URL: %s\n  Body: %s\n"
                "  This usually means ES_URL points to a non-ES service.",
                resp.status_code, bulk_url, resp.text[:1000],
            )
            return 0

        if result.get("errors"):
            error_count = 0
            seen_errors: set[str] = set()
            for item in result.get("items", []):
                err = item.get("index", {}).get("error")
                if err:
                    error_count += 1
                    err_key = f"{err.get('type', '?')}:{err.get('reason', '?')[:100]}"
                    if err_key not in seen_errors:
                        seen_errors.add(err_key)
                        log.error(
                            "ES doc error [%d of %d]:\n"
                            "  type: %s\n  reason: %s\n  caused_by: %s",
                            error_count, len(rows),
                            err.get("type", "?"),
                            err.get("reason", "?"),
                            json.dumps(err.get("caused_by", {})),
                        )
            log.warning(
                "ES bulk index: %d/%d docs FAILED. Unique error types: %s",
                error_count, len(rows), ", ".join(sorted(seen_errors)),
            )
            print(f"\nElasticsearch: {error_count}/{len(rows)} docs FAILED. Check log for details.")
            indexed = len(rows) - error_count
            if indexed > 0:
                print(f"  ({indexed} doc(s) indexed successfully)")
            return indexed

        indexed = len(result.get("items", []))
        log.info("Elasticsearch: indexed %d doc(s) to %s", indexed, self._index)
        print(f"\nElasticsearch: indexed {indexed} doc(s) to {self._index}")
        return indexed


def build_es_client(args: Any) -> ElasticsearchClient | None:
    """Build an :class:`ElasticsearchClient` from *args* / env, or ``None``."""
    es_url = (getattr(args, "es_url", None) or os.environ.get("ES_URL", "")).strip()
    es_index = (getattr(args, "es_index", None) or os.environ.get("ES_INDEX", "")).strip()

    if not es_url or not es_index:
        return None

    api_key = os.environ.get("ES_API_KEY", "").strip()
    username = os.environ.get("ES_USERNAME", "").strip()
    password = os.environ.get("ES_PASSWORD", "").strip()

    return ElasticsearchClient(
        url=es_url, index=es_index,
        api_key=api_key or None,
        username=username or None,
        password=password or None,
        verify_ssl=not getattr(args, "no_verify_ssl", False),
    )
