"""
Settings for cribl_service.
All values read from environment variables — no config.json at request time.
"""
import os

# Cribl Stream connection
CRIBL_BASE_URL: str = os.environ.get("CRIBL_BASE_URL", "").rstrip("/")
CRIBL_TOKEN: str = os.environ.get("CRIBL_TOKEN", "")
CRIBL_USERNAME: str = os.environ.get("CRIBL_USERNAME", "")
CRIBL_PASSWORD: str = os.environ.get("CRIBL_PASSWORD", "")
CRIBL_SKIP_SSL: bool = os.environ.get("CRIBL_SKIP_SSL", "false").lower() in ("1", "true", "yes")

# HTTP behaviour
CRIBL_TIMEOUT: int = int(os.environ.get("CRIBL_TIMEOUT", "30"))

# Defaults for route/workspace operations
CRIBL_DEFAULT_WORKSPACE: str = os.environ.get("CRIBL_DEFAULT_WORKSPACE", "default")
CRIBL_DEFAULT_ROUTES_TABLE: str = os.environ.get("CRIBL_DEFAULT_ROUTES_TABLE", "default")
CRIBL_MIN_EXISTING_ROUTES: int = int(os.environ.get("CRIBL_MIN_EXISTING_ROUTES", "1"))

# Snapshot storage
CRIBL_SNAPSHOT_DIR: str = os.environ.get("CRIBL_SNAPSHOT_DIR", "cribl_snapshots")

# Logging
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()
