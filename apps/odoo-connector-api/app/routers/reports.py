import logging
from fastapi import APIRouter, Depends
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import OdooExecuteReportRequest, OdooListReportsRequest
from app.services.odoo_report_service import OdooReportService

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_client(creds):
    return OdooClient(
        credentials=OdooCredentials(
            url=creds.url,
            db=creds.db,
            username=creds.username,
            password_or_api_key=creds.api_key,
        ),
        transport=creds.transport,
    )


@router.post("/execute")
async def execute_report(req: OdooExecuteReportRequest, auth: dict = Depends(internal_api_key_auth)):
    """Generic endpoint to execute any Odoo accounting report using OdooReportService."""
    client = _get_client(req.credentials)
    service = OdooReportService(client)
    return service.execute(req)


@router.post("/list")
async def list_reports(req: OdooListReportsRequest, auth: dict = Depends(internal_api_key_auth)):
    """List available Odoo account.report records for report discovery."""
    client = _get_client(req.credentials)
    domain = [["name", "ilike", req.query]] if req.query else []
    records = client.search_read(
        model="account.report",
        domain=domain,
        fields=["id", "name"],
        limit=req.limit,
    ) or []
    logger.info(
        "Odoo report list | query=%s limit=%d result_count=%d sample=%s",
        req.query, req.limit, len(records), records[:5],
    )
    return {
        "total": len(records),
        "reports": records,
        "query": req.query,
    }
