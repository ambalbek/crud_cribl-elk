"""
ECEClient — authenticated HTTP client for Elasticsearch and Kibana APIs.

Wraps the pure-logic functions from role_rm.py (generate_templates,
_parse_kibana_console, push_elk, save_templates) which are all safe to
import — none of them call die() / sys.exit() internally.

All HTTP errors are raised as FastAPI HTTPException, never sys.exit().
"""
import base64
import logging
import os
import sys
from typing import Any

import requests
import urllib3
from fastapi import HTTPException

# Make project root importable so role_rm.py and cribl_utils.py resolve
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Safe imports from role_rm.py — none of these call die()
from role_rm import (  # noqa: E402
    generate_templates,
    push_elk,
    save_templates,
)

from .settings import (  # noqa: E402
    ECE_ES_URL, ECE_ES_TOKEN, ECE_ES_USERNAME, ECE_ES_PASSWORD,
    ECE_ES_URL_PROD, ECE_ES_TOKEN_PROD, ECE_ES_USERNAME_PROD, ECE_ES_PASSWORD_PROD,
    ECE_KIBANA_URL, ECE_KIBANA_TOKEN, ECE_KIBANA_USERNAME, ECE_KIBANA_PASSWORD,
    ECE_SKIP_SSL, TEMPLATES_OUTPUT_DIR,
)

logger = logging.getLogger("ece-service")

# Default configurations matching role_rm.py's hardcoded list.
# Override per-request by passing `configurations` in the provision body.
DEFAULT_CONFIGURATIONS = [
    {"region": "onshore",  "environment": "test", "domain": "adhcsctst", "roles": ["watcher_user"]},
    {"region": "offshore", "environment": "test", "domain": "adhcsctst", "roles": ["watcher_user"]},
    {"region": "onshore",  "environment": "prod", "domain": "adhcscint", "roles": ["watcher_admin"]},
    {"region": "offshore", "environment": "prod", "domain": "adhcscint", "roles": ["watcher_admin"]},
]


# ── Session / header helpers ──────────────────────────────────────────────────

def _build_session(skip_ssl: bool = ECE_SKIP_SSL) -> requests.Session:
    if skip_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    s = requests.Session()
    s.verify = not skip_ssl
    s.proxies = {"http": "", "https": ""}
    return s


def _make_headers(token: str, username: str, password: str) -> dict[str, str]:
    base = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        return {**base, "Authorization": f"ApiKey {token}"}
    if username and password:
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {**base, "Authorization": f"Basic {creds}"}
    return base


# ── ECEClient ─────────────────────────────────────────────────────────────────

class ECEClient:
    """
    One instance per request (created by get_ece_client dependency).

    Exposes methods for:
      - ES security roles / role-mappings  (wraps role_rm.py logic)
      - ES index management
      - Logstash pipelines stored in ES
      - Kibana dashboards (via Kibana saved objects API)
      - App provisioning  (calls push_elk from role_rm.py)
    """

    def __init__(self) -> None:
        if not ECE_ES_URL:
            raise HTTPException(status_code=500, detail="ECE_ES_URL env var is not set")

        self._es_base       = ECE_ES_URL
        self._es_base_prod  = ECE_ES_URL_PROD or ECE_ES_URL
        self._kibana_base   = ECE_KIBANA_URL

        self._session       = _build_session()
        self._es_headers    = _make_headers(ECE_ES_TOKEN,      ECE_ES_USERNAME,      ECE_ES_PASSWORD)
        self._es_prod_hdrs  = _make_headers(ECE_ES_TOKEN_PROD, ECE_ES_USERNAME_PROD, ECE_ES_PASSWORD_PROD)
        self._kib_headers   = _make_headers(ECE_KIBANA_TOKEN,  ECE_KIBANA_USERNAME,  ECE_KIBANA_PASSWORD)
        # Kibana also needs the kbn-xsrf header for write operations
        self._kib_headers["kbn-xsrf"] = "true"

    # ── Low-level call helpers ────────────────────────────────────────────────

    def _raise(self, r: requests.Response, path: str) -> dict[str, Any]:
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code,
                                detail=f"{path}: {r.text[:400]}")
        return r.json() if r.content else {}

    def _es(self, method: str, path: str, **kw) -> dict[str, Any]:
        url = self._es_base + path
        try:
            r = self._session.request(method, url, headers=self._es_headers, timeout=60, **kw)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"ES {method} {path}: {exc}")
        return self._raise(r, path)

    def _es_prod(self, method: str, path: str, **kw) -> dict[str, Any]:
        url = self._es_base_prod + path
        try:
            r = self._session.request(method, url, headers=self._es_prod_hdrs, timeout=60, **kw)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"ES-prod {method} {path}: {exc}")
        return self._raise(r, path)

    def _kibana(self, method: str, path: str, **kw) -> dict[str, Any]:
        if not self._kibana_base:
            raise HTTPException(status_code=500, detail="ECE_KIBANA_URL is not set")
        url = self._kibana_base + path
        try:
            r = self._session.request(method, url, headers=self._kib_headers, timeout=60, **kw)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Kibana {method} {path}: {exc}")
        return self._raise(r, path)

    # ── ES Security Roles ─────────────────────────────────────────────────────

    def list_roles(self, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("GET", "/_security/role")

    def get_role(self, name: str, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("GET", f"/_security/role/{name}")

    def put_role(self, name: str, body: dict[str, Any], target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("PUT", f"/_security/role/{name}", json=body)

    def delete_role(self, name: str, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("DELETE", f"/_security/role/{name}")

    # ── ES Security Role-Mappings ─────────────────────────────────────────────

    def list_role_mappings(self, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("GET", "/_security/role_mapping")

    def get_role_mapping(self, name: str, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("GET", f"/_security/role_mapping/{name}")

    def put_role_mapping(self, name: str, body: dict[str, Any], target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("PUT", f"/_security/role_mapping/{name}", json=body)

    def delete_role_mapping(self, name: str, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("DELETE", f"/_security/role_mapping/{name}")

    # ── ES Indexes ────────────────────────────────────────────────────────────

    def list_indexes(self, pattern: str = "*", target: str = "nonprod") -> list:
        call = self._es_prod if target == "prod" else self._es
        # _cat/indices returns a list
        url  = self._es_base_prod if target == "prod" else self._es_base
        try:
            r = self._session.get(
                f"{url}/_cat/indices/{pattern}",
                headers=(self._es_prod_hdrs if target == "prod" else self._es_headers),
                params={"format": "json", "h": "index,health,status,pri,rep,docs.count,store.size"},
                timeout=60,
            )
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"ES list_indexes: {exc}")
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text[:400])
        return r.json()

    def get_index(self, name: str, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("GET", f"/{name}")

    def put_index(self, name: str, body: dict[str, Any], target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("PUT", f"/{name}", json=body)

    def delete_index(self, name: str, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("DELETE", f"/{name}")

    def get_index_template(self, name: str, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("GET", f"/_index_template/{name}")

    def put_index_template(self, name: str, body: dict[str, Any], target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("PUT", f"/_index_template/{name}", json=body)

    # ── Logstash Pipelines (stored in ES) ─────────────────────────────────────

    def list_logstash_pipelines(self, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("GET", "/_logstash/pipeline")

    def get_logstash_pipeline(self, pipeline_id: str, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("GET", f"/_logstash/pipeline/{pipeline_id}")

    def put_logstash_pipeline(self, pipeline_id: str, body: dict[str, Any], target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("PUT", f"/_logstash/pipeline/{pipeline_id}", json=body)

    def delete_logstash_pipeline(self, pipeline_id: str, target: str = "nonprod") -> dict[str, Any]:
        call = self._es_prod if target == "prod" else self._es
        return call("DELETE", f"/_logstash/pipeline/{pipeline_id}")

    # ── Kibana Dashboards ─────────────────────────────────────────────────────

    def list_dashboards(self, search: str = "", page: int = 1, per_page: int = 50) -> dict[str, Any]:
        params: dict[str, Any] = {"type": "dashboard", "page": page, "per_page": per_page}
        if search:
            params["search"] = search
            params["search_fields"] = "title"
        url = self._kibana_base + "/api/saved_objects/_find"
        try:
            r = self._session.get(url, headers=self._kib_headers, params=params, timeout=60)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Kibana list_dashboards: {exc}")
        return self._raise(r, "/api/saved_objects/_find")

    def get_dashboard(self, dashboard_id: str) -> dict[str, Any]:
        return self._kibana("GET", f"/api/saved_objects/dashboard/{dashboard_id}")

    def create_dashboard(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._kibana("POST", "/api/saved_objects/dashboard", json=body)

    def update_dashboard(self, dashboard_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._kibana("PUT", f"/api/saved_objects/dashboard/{dashboard_id}", json=body)

    def delete_dashboard(self, dashboard_id: str) -> dict[str, Any]:
        return self._kibana("DELETE", f"/api/saved_objects/dashboard/{dashboard_id}")

    # ── App Provisioning (wraps role_rm.push_elk) ─────────────────────────────

    def generate_app_templates(
        self,
        app_name: str,
        apmid: str,
        configurations: list[dict] | None = None,
        save_to_disk: bool = True,
    ) -> dict[str, Any]:
        """
        Generate ELK role + role-mapping templates using role_rm.generate_templates.
        Optionally saves them to TEMPLATES_OUTPUT_DIR (same as role_rm.py behaviour).
        Returns a dict with rendered template strings grouped by config.
        """
        cfgs = configurations or DEFAULT_CONFIGURATIONS
        apps = [(app_name, apmid)]
        result: list[dict] = []

        for cfg in cfgs:
            role_r, rm_r = generate_templates(
                app_name, apmid,
                cfg["environment"], cfg["region"],
                "PUSER", cfg["domain"], cfg["roles"],
            )
            role_u, rm_u = generate_templates(
                app_name, apmid,
                cfg["environment"], cfg["region"],
                "USER", cfg["domain"], cfg["roles"],
            )
            result.append({
                "environment": cfg["environment"],
                "region": cfg["region"],
                "role_PUSER": role_r,
                "role_USER":  role_u,
                "role_mapping_PUSER": rm_r,
                "role_mapping_USER":  rm_u,
            })

        if save_to_disk:
            save_templates(apps, cfgs)

        return {"app_name": app_name, "apmid": apmid, "templates": result}

    def provision_app(
        self,
        app_name: str,
        apmid: str,
        configurations: list[dict] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Generate and push ELK roles + role-mappings for both nonprod and prod clusters.
        Wraps role_rm.push_elk() directly — returns success bool + any errors logged.
        """
        if not ECE_ES_URL:
            raise HTTPException(status_code=500, detail="ECE_ES_URL is required for provisioning")
        if not ECE_ES_URL_PROD:
            raise HTTPException(status_code=500, detail="ECE_ES_URL_PROD is required for provisioning")

        cfgs  = configurations or DEFAULT_CONFIGURATIONS
        apps  = [(app_name, apmid)]

        session_np  = _build_session()
        session_p   = _build_session()
        headers_np  = _make_headers(ECE_ES_TOKEN,      ECE_ES_USERNAME,      ECE_ES_PASSWORD)
        headers_p   = _make_headers(ECE_ES_TOKEN_PROD, ECE_ES_USERNAME_PROD, ECE_ES_PASSWORD_PROD)

        # save_templates always runs (role_rm.py original behaviour)
        save_templates(apps, cfgs)

        ok = push_elk(
            apps, cfgs,
            ECE_ES_URL, ECE_ES_URL_PROD,
            session_np, headers_np,
            session_p,  headers_p,
            dry_run, logger,
        )

        return {
            "app_name": app_name,
            "apmid": apmid,
            "dry_run": dry_run,
            "success": ok,
            "templates_saved_to": TEMPLATES_OUTPUT_DIR,
        }


def get_ece_client() -> ECEClient:
    """FastAPI dependency — new authenticated client per request."""
    return ECEClient()
