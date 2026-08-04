from app.services.cribl_client import CriblClient
from app.services.etn_portal_client import ETNPortalClient
from app.services.harness_client import HarnessClient

__all__ = [
    "CriblClient",
    "ETNPortalClient",
    "HarnessClient",
]

# Lazy-initialised singleton instances.  Call ``init_services(app)`` from
# the application factory to configure them from Flask config / env vars.
# Until then the module-level references are ``None`` and route handlers
# should check before use.

cribl: CriblClient | None = None
etn_portal: ETNPortalClient | None = None
harness: HarnessClient | None = None


def init_services(flask_app) -> None:
    """Initialise service client singletons from Flask application config.

    Expected config keys (all optional, stubs will be used with dummy URLs
    if not set):
      - ``CRIBL_BASE_URL``, ``CRIBL_TOKEN``
      - ``ETN_PORTAL_URL``, ``ETN_PORTAL_API_KEY``
      - ``HARNESS_BASE_URL``, ``HARNESS_API_KEY``, ``HARNESS_ACCOUNT_ID``
    """
    global cribl, etn_portal, harness

    cribl = CriblClient(
        base_url=flask_app.config.get("CRIBL_BASE_URL", "http://localhost:9000"),
        token=flask_app.config.get("CRIBL_TOKEN", ""),
    )
    etn_portal = ETNPortalClient(
        base_url=flask_app.config.get("ETN_PORTAL_URL", "http://localhost:8080"),
        api_key=flask_app.config.get("ETN_PORTAL_API_KEY"),
    )
    harness = HarnessClient(
        base_url=flask_app.config.get("HARNESS_BASE_URL", "http://localhost:8090"),
        api_key=flask_app.config.get("HARNESS_API_KEY"),
        account_id=flask_app.config.get("HARNESS_ACCOUNT_ID"),
    )
