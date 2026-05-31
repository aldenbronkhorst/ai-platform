from pydantic import BaseModel, Field
from typing import Any, Optional


class OdooCredentialsRequest(BaseModel):
    url: str = Field(..., description="Odoo instance URL")
    db: str = Field(..., description="Odoo database name")
    username: str = Field(..., description="Odoo username")
    api_key: str = Field(..., description="Odoo API key or password")
    transport: str = Field(default="auto", description="Transport: auto, xmlrpc, jsonrpc")


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
