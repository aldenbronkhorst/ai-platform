import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "AI Platform Core API"
    app_version: str = "0.1.0"
    debug: bool = os.environ.get("DEBUG", "false").lower() == "true"

    # Database
    postgres_host: str = os.environ.get("POSTGRES_HOST", "localhost")
    postgres_port: int = int(os.environ.get("POSTGRES_PORT", "5432"))
    postgres_user: str = os.environ.get("POSTGRES_USER", "aiplatformadmin")
    postgres_password: str = os.environ.get("POSTGRES_PASSWORD", "")
    postgres_db: str = os.environ.get("POSTGRES_DB", "aicore")

    # Azure
    azure_client_id: str = os.environ.get("AZURE_CLIENT_ID", "")
    key_vault_uri: str = os.environ.get("KEY_VAULT_URI", "")
    storage_account_name: str = os.environ.get("STORAGE_ACCOUNT_NAME", "")
    service_bus_namespace: str = os.environ.get("AZURE_SERVICE_BUS_NAMESPACE") or os.environ.get("SERVICE_BUS_NAMESPACE", "")
    appinsights_connection_string: str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")

    # Auth (temporary — replace with Entra ID / JWT)
    api_key: str = os.environ.get("API_KEY", "change-in-production")

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def sync_database_url(self) -> str:
        return f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
