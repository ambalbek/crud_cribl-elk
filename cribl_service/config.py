"""
Cribl service configuration via pydantic-settings.
All values read from environment variables with CRIBL_ prefix.
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CriblSettings(BaseSettings):
    """Typed settings for cribl_service — sourced exclusively from env vars."""

    model_config = SettingsConfigDict(
        env_prefix="CRIBL_",
        case_sensitive=False,
        extra="ignore",
    )

    base_url: str = Field(default="", description="Cribl Stream leader base URL (e.g. https://cribl.example.com:9000)")
    token: str = Field(default="", description="Cribl bearer token — preferred over username/password")
    username: str = Field(default="", description="Cribl username for password-based auth")
    password: str = Field(default="", description="Cribl password for password-based auth")
    skip_ssl: bool = Field(default=False, description="Disable TLS certificate verification")
    timeout: int = Field(default=30, description="HTTP request timeout in seconds")
    default_workspace: str = Field(default="default", description="Default workspace name when none is specified")
    default_routes_table: str = Field(default="default", description="Default route table name")
    min_existing_routes: int = Field(default=1, description="Safety check: refuse to PATCH if fewer routes than this exist")
    snapshot_dir: str = Field(default="cribl_snapshots", description="Directory for pre-patch route snapshots")
    log_level: str = Field(default="INFO", description="Logging verbosity: DEBUG | INFO | WARNING | ERROR")


settings = CriblSettings()
