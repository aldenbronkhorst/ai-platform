from fastapi import APIRouter, Depends
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import OdooExecuteReportRequest
from app.services.odoo_report_service import OdooReportService

router = APIRouter()


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
