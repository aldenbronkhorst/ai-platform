from typing import Any, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class OdooCredentialsRequest(BaseModel):
    url: str = Field(..., description="Odoo instance URL")
    db: str = Field(..., description="Odoo database name")
    username: str = Field(..., description="Odoo username")
    api_key: str = Field(..., description="Odoo API key or password")
    transport: Literal["auto", "xmlrpc", "jsonrpc"] = Field(default="auto", description="Transport: auto, xmlrpc, jsonrpc")

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


class OdooExecuteReportRequest(BaseModel):
    credentials: OdooCredentialsRequest
    report_name: str
    report_id: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    company_id: Optional[int] = None
    timezone: Optional[str] = None
    lang: Optional[str] = None
    line_names: Optional[list[str]] = None
    include_raw_lines: bool = False
