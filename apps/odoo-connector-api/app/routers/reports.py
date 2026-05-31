from fastapi import APIRouter, Depends, HTTPException, status
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import OdooProfitAndLossRequest

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


@router.post("/profit-and-loss")
async def get_profit_and_loss(req: OdooProfitAndLossRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)
    
    # Try official account.report first
    try:
        report_ids = client.call_with_transport(
            "account.report", 
            "search", 
            [[["report_type", "=", "profit_and_loss"]]]
        )
        if not report_ids:
            report_ids = client.call_with_transport(
                "account.report", 
                "search", 
                [[["name", "ilike", "profit and loss"]]]
            )
            
        if report_ids:
            report_id = report_ids[0]
            options = {}
            if req.date_from:
                options["date"] = {"date_from": req.date_from, "date_to": req.date_to, "filter": "custom"}
            if req.company_id:
                options["company_id"] = req.company_id
                
            report_info = client.call_with_transport(
                "account.report", 
                "get_report_informations", 
                [report_id, options]
            )
            lines = report_info.get("lines", [])
            total_revenue = 0.0
            
            for line in lines:
                name = str(line.get("name", "")).lower()
                if any(k in name for k in ["revenue", "turnover", "income", "total revenue"]):
                    total_revenue = float(line.get("columns", [{}])[0].get("no_format_name", 0.0) or total_revenue)
            
            currency_code = req.currency or "USD"
            symbols = {"USD": "$", "ZAR": "R", "EUR": "€", "GBP": "£"}
            currency_symbol = symbols.get(currency_code, currency_code)
            
            return {
                "report": "Profit and Loss",
                "date_from": req.date_from,
                "date_to": req.date_to,
                "currency_code": currency_code,
                "currency_symbol": currency_symbol,
                "revenue": {
                    "value": total_revenue,
                    "formatted": f"{currency_symbol} {total_revenue:,.2f}",
                    "source": "odoo_account_report"
                },
                "lines": lines
            }
    except Exception:
        # Fall back to customer invoices
        pass
        
    # Fallback path: posted customer invoices
    try:
        domain = [
            ("state", "=", "posted"),
            ("move_type", "=", "out_invoice")
        ]
        if req.date_from:
            domain.append(("invoice_date", ">=", req.date_from))
        if req.date_to:
            domain.append(("invoice_date", "<=", req.date_to))
        if req.company_id:
            domain.append(("company_id", "=", req.company_id))

        try:
            moves = client.search_read(
                model="account.move",
                domain=domain,
                fields=["id", "name", "amount_untaxed", "invoice_date", "currency_id"]
            )
        except Exception:
            # Fallback to older Odoo invoice schema
            domain_old = [
                ("state", "=", "posted"),
                ("type", "=", "out_invoice")
            ]
            if req.date_from:
                domain_old.append(("date_invoice", ">=", req.date_from))
            if req.date_to:
                domain_old.append(("date_invoice", "<=", req.date_to))
            if req.company_id:
                domain_old.append(("company_id", "=", req.company_id))
                
            moves = client.search_read(
                model="account.invoice",
                domain=domain_old,
                fields=["id", "number", "amount_untaxed", "date_invoice", "currency_id"]
            )
            
        total_revenue = sum(float(move.get("amount_untaxed", 0.0)) for move in moves)
        currency_code = req.currency or "USD"
        if moves:
            currency_val = moves[0].get("currency_id")
            if isinstance(currency_val, list) and len(currency_val) == 2:
                currency_code = currency_val[1]
            elif isinstance(currency_val, dict) and "name" in currency_val:
                currency_code = currency_val["name"]
                
        symbols = {"USD": "$", "ZAR": "R", "EUR": "€", "GBP": "£"}
        currency_symbol = symbols.get(currency_code, currency_code)
        
        return {
            "report": "Profit and Loss (Fallback)",
            "date_from": req.date_from,
            "date_to": req.date_to,
            "currency_code": currency_code,
            "currency_symbol": currency_symbol,
            "revenue": {
                "value": total_revenue,
                "formatted": f"{currency_symbol} {total_revenue:,.2f}",
                "source": "fallback_posted_customer_invoices",
                "warning": "Could not access official Odoo P&L report. Revenue was calculated from posted customer invoices for the selected period."
            },
            "lines": []
        }
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "report_unsupported",
                "message": f"Both official P&L report and posted customer invoice fallback queries failed. Error: {str(e)}",
                "method_attempted": "account.report / account.move / account.invoice"
            }
        )
