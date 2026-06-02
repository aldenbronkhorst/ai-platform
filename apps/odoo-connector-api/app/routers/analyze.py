import logging
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import AnalyzeRequest
from app.services.odoo_report_service import OdooReportService

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_client(creds):
    return OdooClient(
        credentials=OdooCredentials(
            url=creds.url, db=creds.db, username=creds.username,
            password_or_api_key=creds.api_key,
        ),
        transport=creds.transport,
    )


@router.post("/analyze")
def analyze(req: AnalyzeRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)

    if req.mode == "aggregate":
        if not req.model or not req.groupby:
            raise HTTPException(status_code=400, detail={"error": "missing_params", "message": "aggregate mode requires model and groupby"})
        result = client.call_with_transport(
            req.model, "read_group",
            args=[req.domain or [], req.fields or [], req.groupby],
            kwargs={"lazy": req.lazy},
        )
        return {"model": req.model, "groupby": req.groupby, "groups": result}

    if req.mode == "account_report":
        if not req.report_name and not req.report_id:
            raise HTTPException(status_code=400, detail={"error": "missing_params", "message": "account_report mode requires report_name or report_id"})
        from app.models.schemas import OdooExecuteReportRequest
        report_req = OdooExecuteReportRequest(
            credentials=req.credentials,
            report_name=req.report_name or "",
            report_id=req.report_id,
            date_from=req.date_from,
            date_to=req.date_to,
            company_id=req.company_id,
            timezone=req.timezone,
            lang=req.lang,
            line_names=req.line_names,
            include_raw_lines=req.include_raw_lines,
        )
        service = OdooReportService(client)
        return service.execute(report_req)

    raise HTTPException(status_code=400, detail={"error": "unknown_mode", "message": f"Unknown mode: {req.mode}"})
