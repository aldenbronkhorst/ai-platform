from fastapi import APIRouter, Depends
from app.core.config import get_settings
from app.core.security import internal_api_key_auth
from app.models.schemas import HealthResponse, CapabilitiesResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        version=settings.app_version,
        capabilities=[
            "schema.models",
            "schema.fields",
            "records.search-read",
            "records.count",
            "records.read",
            "records.mutate",
            "execute-kw",
            "attachments.list",
            "attachments.get",
            "attachments.create",
            "messages.list",
            "messages.create",
            "reports.execute",
        ],
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(auth: dict = Depends(internal_api_key_auth)):
    settings = get_settings()
    return CapabilitiesResponse(
        endpoints=[
            {"path": "/schema/models", "method": "POST", "description": "Search Odoo models"},
            {"path": "/schema/fields", "method": "POST", "description": "Inspect model fields"},
            {"path": "/records/search-read", "method": "POST", "description": "Search and read records"},
            {"path": "/records/count", "method": "POST", "description": "Count records matching domain"},
            {"path": "/records/read", "method": "POST", "description": "Read specific record IDs"},
            {"path": "/records/mutate", "method": "POST", "description": "Create/write/delete/workflow records"},
            {"path": "/execute-kw", "method": "POST", "description": "Generic execute_kw (gated)"},
            {"path": "/attachments/list", "method": "POST", "description": "List attachments"},
            {"path": "/attachments/get", "method": "POST", "description": "Get attachment metadata/content"},
            {"path": "/attachments/create", "method": "POST", "description": "Create attachment on record"},
            {"path": "/messages/list", "method": "POST", "description": "List messages/chatter"},
            {"path": "/messages/create", "method": "POST", "description": "Post message to record chatter"},
        ],
        execute_kw_enabled=settings.execute_kw_allow_write_methods,
        execute_kw_write_methods=settings.execute_kw_allow_write_methods,
    )
