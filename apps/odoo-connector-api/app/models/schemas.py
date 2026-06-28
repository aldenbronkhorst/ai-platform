from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class OdooCredentialsRequest(BaseModel):
    url: str = Field(..., description="Odoo instance URL")
    db: str = Field(..., description="Odoo database name")
    username: str = Field(..., description="Odoo username")
    api_key: str = Field(..., description="Odoo API key or password")
    transport: Literal["auto", "jsonrpc", "xmlrpc"] = Field(
        default="auto",
        description="Transport: auto, jsonrpc, xmlrpc. Auto uses Odoo JSON-RPC.",
    )

    @field_validator("url")
    @classmethod
    def validate_url(_cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URL must use http or https scheme")
        if not parsed.hostname:
            raise ValueError("URL must include a hostname")
        return value


class CapabilitiesResponse(BaseModel):
    endpoints: list[dict[str, Any]]
    execute_kw_enabled: bool
    execute_kw_write_methods: bool
