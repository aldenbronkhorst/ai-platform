import os
from functools import lru_cache
from urllib.parse import quote_plus
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


def _default_app_env() -> str:
    return os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT", "development")


def _normalize_app_env(value: str | None) -> str:
    normalized = (value or "development").strip().lower()
    if normalized in {"prod", "production"}:
        return "production"
    if normalized in {"dev", "local", "development"}:
        return "development"
    return normalized


class Settings(BaseSettings):
    app_name: str = "AI Platform Core API"
    app_version: str = "0.1.0"
    app_env: str = Field(default_factory=_default_app_env)
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
    azure_storage_connection_string: str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    azure_storage_account_key: str = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY", "")
    appinsights_connection_string: str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    azure_document_intelligence_endpoint: str = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "")
    azure_document_intelligence_key: str = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY", "")
    document_ocr_provider: str = os.environ.get("DOCUMENT_OCR_PROVIDER", "azure_document_intelligence")
    document_ocr_read_model_id: str = os.environ.get("DOCUMENT_OCR_READ_MODEL_ID", "prebuilt-read")
    document_ocr_layout_model_id: str = os.environ.get("DOCUMENT_OCR_LAYOUT_MODEL_ID", "prebuilt-layout")
    document_extraction_max_chars: int = int(os.environ.get("DOCUMENT_EXTRACTION_MAX_CHARS", "500000"))
    document_layout_max_tables: int = int(os.environ.get("DOCUMENT_LAYOUT_MAX_TABLES", "80"))
    document_layout_max_cells: int = int(os.environ.get("DOCUMENT_LAYOUT_MAX_CELLS", "8000"))
    document_layout_max_lines: int = int(os.environ.get("DOCUMENT_LAYOUT_MAX_LINES", "5000"))
    document_layout_cell_max_chars: int = int(os.environ.get("DOCUMENT_LAYOUT_CELL_MAX_CHARS", "1000"))
    document_layout_markdown_max_chars: int = int(os.environ.get("DOCUMENT_LAYOUT_MARKDOWN_MAX_CHARS", "120000"))
    attachment_preview_max_chars: int = int(os.environ.get("ATTACHMENT_PREVIEW_MAX_CHARS", "24000"))

    # Internal API key for service-to-service calls; user auth uses Entra JWT.
    api_key: str = os.environ.get("API_KEY", "")

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_app_env(_cls, value: str | None) -> str:
        return _normalize_app_env(value)

    @property
    def database_url(self) -> str:
        pw = quote_plus(self.postgres_password)
        return f"postgresql+asyncpg://{self.postgres_user}:{pw}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
