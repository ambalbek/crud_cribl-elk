"""
Settings for ece_service.
All values read from environment variables — no config.json at request time.

Two ES targets are supported (nonprod + prod) for the /provision endpoint
that mirrors role_rm.py's dual-cluster push behaviour.
"""
import os

# ── Elasticsearch — nonprod ──────────────────────────────────────────────────
ECE_ES_URL: str      = os.environ.get("ECE_ES_URL", "").rstrip("/")
ECE_ES_TOKEN: str    = os.environ.get("ECE_ES_TOKEN", "")      # ApiKey, overrides user/pass
ECE_ES_USERNAME: str = os.environ.get("ECE_ES_USERNAME", "")
ECE_ES_PASSWORD: str = os.environ.get("ECE_ES_PASSWORD", "")

# ── Elasticsearch — prod ─────────────────────────────────────────────────────
ECE_ES_URL_PROD: str      = os.environ.get("ECE_ES_URL_PROD", "").rstrip("/")
ECE_ES_TOKEN_PROD: str    = os.environ.get("ECE_ES_TOKEN_PROD", "")
ECE_ES_USERNAME_PROD: str = os.environ.get("ECE_ES_USERNAME_PROD", "")
ECE_ES_PASSWORD_PROD: str = os.environ.get("ECE_ES_PASSWORD_PROD", "")

# ── Kibana ────────────────────────────────────────────────────────────────────
ECE_KIBANA_URL: str      = os.environ.get("ECE_KIBANA_URL", "").rstrip("/")
ECE_KIBANA_TOKEN: str    = os.environ.get("ECE_KIBANA_TOKEN", "")
ECE_KIBANA_USERNAME: str = os.environ.get("ECE_KIBANA_USERNAME", "")
ECE_KIBANA_PASSWORD: str = os.environ.get("ECE_KIBANA_PASSWORD", "")

# ── Shared ────────────────────────────────────────────────────────────────────
ECE_SKIP_SSL: bool = os.environ.get("ECE_SKIP_SSL", "false").lower() in ("1", "true", "yes")
LOG_LEVEL: str     = os.environ.get("LOG_LEVEL", "INFO").upper()

# Output directory for generated ELK templates (same as role_rm.py default)
TEMPLATES_OUTPUT_DIR: str = os.environ.get("ECE_TEMPLATES_DIR", "ops_rm_r_templates_output")
