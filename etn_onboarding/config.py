import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql://etn_user:etn_pass@localhost:5432/etn_onboarding"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Service client URLs
    CRIBL_BASE_URL = os.environ.get("CRIBL_BASE_URL", "")
    CRIBL_TOKEN = os.environ.get("CRIBL_TOKEN", "")
    ECE_ES_URL = os.environ.get("ECE_ES_URL", "")
    ECE_ES_TOKEN = os.environ.get("ECE_ES_TOKEN", "")
    ETN_PORTAL_URL = os.environ.get("ETN_PORTAL_URL", "")
    ETN_PORTAL_API_KEY = os.environ.get("ETN_PORTAL_API_KEY", "")
    HARNESS_BASE_URL = os.environ.get("HARNESS_BASE_URL", "")
    HARNESS_API_KEY = os.environ.get("HARNESS_API_KEY", "")
    HARNESS_ACCOUNT_ID = os.environ.get("HARNESS_ACCOUNT_ID", "")

    # OTel
    OTEL_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "etn-onboarding")
    OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
