import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Odoo Connector API"
    app_version: str = "1.0.0"
    debug: bool = os.environ.get("DEBUG", "false").lower() == "true"

    # Internal service auth
    internal_api_key: str = os.environ.get("INTERNAL_API_KEY", "change-me-in-production")

    # Odoo defaults (can be overridden per-request)
    odoo_url: str = os.environ.get("ODOO_URL", "")
    odoo_db: str = os.environ.get("ODOO_DB", "")
    odoo_username: str = os.environ.get("ODOO_USERNAME", "")
    odoo_api_key: str = os.environ.get("ODOO_API_KEY", "")
    odoo_api_transport: str = os.environ.get("ODOO_API_TRANSPORT", "auto")
    odoo_api_timeout_seconds: float = float(os.environ.get("ODOO_API_TIMEOUT_SECONDS", "120.0"))
    odoo_ssl_verify: bool = os.environ.get("ODOO_SSL_VERIFY", "true").lower() == "true"

    # Permission gates
    execute_kw_allow_write_methods: bool = os.environ.get("EXECUTE_KW_ALLOW_WRITE", "false").lower() == "true"
    execute_kw_blocked_methods: str = os.environ.get("EXECUTE_KW_BLOCKED_METHODS", "")

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
