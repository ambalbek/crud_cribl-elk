"""
Pydantic-settings configuration for ece_service.
All values loaded from ECE_* environment variables.
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ECESettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ECE_",
        case_sensitive=False,
        extra="ignore",
    )

    # Elasticsearch — nonprod
    es_url: str = Field(default="", description="Elasticsearch nonprod base URL")
    es_token: str = Field(default="", description="ApiKey for ES nonprod (overrides user/pass)")
    es_username: str = Field(default="", description="ES nonprod username")
    es_password: str = Field(default="", description="ES nonprod password")

    # Elasticsearch — prod
    es_url_prod: str = Field(default="", description="Elasticsearch prod base URL")
    es_token_prod: str = Field(default="", description="ApiKey for ES prod")
    es_username_prod: str = Field(default="", description="ES prod username")
    es_password_prod: str = Field(default="", description="ES prod password")

    # Kibana
    kibana_url: str = Field(default="", description="Kibana base URL")
    kibana_token: str = Field(default="", description="ApiKey for Kibana")
    kibana_username: str = Field(default="", description="Kibana username")
    kibana_password: str = Field(default="", description="Kibana password")

    # Shared
    skip_ssl: bool = Field(default=False, description="Disable SSL certificate verification")
    timeout: int = Field(default=60, description="HTTP request timeout in seconds")
    templates_dir: str = Field(
        default="ops_rm_r_templates_output",
        description="Output directory for generated ELK role/role-mapping templates",
    )
    log_level: str = Field(default="INFO", description="Logging level")


settings = ECESettings()
