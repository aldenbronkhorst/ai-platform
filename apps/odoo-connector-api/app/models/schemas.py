from pydantic import BaseModel, Field, field_validator
from typing import Any, Optional, Literal
from urllib.parse import urlparse


class OdooCredentialsRequest(BaseModel):
    url: str = Field(..., description="Odoo instance URL")
    db: str = Field(..., description="Odoo database name")
    username: str = Field(..., description="Odoo username")
    api_key: str = Field(..., description="Odoo API key or password")
    transport: Literal["auto", "xmlrpc", "jsonrpc"] = Field(default="auto", description="Transport: auto, xmlrpc, jsonrpc")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URL must use http or https scheme")
        hostname = parsed.hostname or ""
        blocked_prefixes = ("169.254.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
                           "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
                           "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
                           "192.168.", "127.", "0.")
        if any(hostname.startswith(p) for p in blocked_prefixes) or hostname in ("localhost", "metadata.google.internal"):
            raise ValueError("URL must not target internal/private network addresses")
        return v


class HealthResponse(BaseModel):
    status: str
    version: str
    capabilities: list[str]


class CapabilitiesResponse(BaseModel):
    endpoints: list[dict[str, Any]]
    execute_kw_enabled: bool
    execute_kw_write_methods: bool


class SchemaModelsRequest(BaseModel):
    credentials: OdooCredentialsRequest
    query: Optional[str] = None
    limit: int = 50


class SchemaFieldsRequest(BaseModel):
    credentials: OdooCredentialsRequest
    model: str
    fields: Optional[list[str]] = None
    attributes: Optional[list[str]] = None


class RecordsSearchReadRequest(BaseModel):
    credentials: OdooCredentialsRequest
    model: str
    domain: Optional[list[Any]] = None
    fields: Optional[list[str]] = None
    limit: int = 50
    offset: int = 0
    order: Optional[str] = None
    include_ids: bool = False


class RecordsCountRequest(BaseModel):
    credentials: OdooCredentialsRequest
    model: str
    domain: Optional[list[Any]] = None


class RecordsReadRequest(BaseModel):
    credentials: OdooCredentialsRequest
    model: str
    ids: list[int]
    fields: Optional[list[str]] = None


class RecordsMutateRequest(BaseModel):
    credentials: OdooCredentialsRequest
    model: str
    operation: str = Field(..., description="create, write, delete, workflow")
    record_ids: Optional[list[int]] = None
    values: Optional[dict[str, Any]] = None
    workflow_method: Optional[str] = None
    dry_run: bool = False
    verify: bool = True
    verify_fields: Optional[list[str]] = None


class ExecuteKwRequest(BaseModel):
    credentials: OdooCredentialsRequest
    model: str
    method: str
    args: Optional[list[Any]] = None
    kwargs: Optional[dict[str, Any]] = None
    dry_run: bool = False


class AttachmentListRequest(BaseModel):
    credentials: OdooCredentialsRequest
    model: Optional[str] = None
    record_id: Optional[int] = None
    domain: Optional[list[Any]] = None
    limit: int = 50


class AttachmentGetRequest(BaseModel):
    credentials: OdooCredentialsRequest
    attachment_id: int
    mode: str = Field(default="metadata", description="metadata, base64, text")


class AttachmentCreateRequest(BaseModel):
    credentials: OdooCredentialsRequest
    model: str
    record_id: int
    filename: str
    content_base64: str
    mimetype: Optional[str] = None


class MessageListRequest(BaseModel):
    credentials: OdooCredentialsRequest
    model: Optional[str] = None
    record_id: Optional[int] = None
    domain: Optional[list[Any]] = None
    limit: int = 50


class MessageCreateRequest(BaseModel):
    credentials: OdooCredentialsRequest
    model: str
    record_id: int
    body: str
    subtype_xmlid: str = "mail.mt_comment"
    message_type: str = "comment"
    partner_ids: Optional[list[int]] = None
    attachment_ids: Optional[list[int]] = None


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
