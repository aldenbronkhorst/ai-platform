from fastapi import APIRouter, Depends, HTTPException, status
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import OdooExecuteReportRequest, OdooProfitAndLossRequest
from typing import Optional, Any

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


def _map_report_name(name: str) -> str:
    """Map common aliases to official Odoo account.report names."""
    n = name.strip().lower()
    if n in {"p&l", "pnl", "profit and loss", "profit & loss", "profit_and_loss"}:
        return "Profit and Loss"
    if n in {"balance sheet", "balancesheet", "bs"}:
        return "Balance Sheet"
    if n in {"trial balance", "trialbalance", "tb"}:
        return "Trial Balance"
    if n in {"general ledger", "generalledger", "gl"}:
        return "General Ledger"
    if n in {"partner ledger", "partnerledger"}:
        return "Partner Ledger"
    if n in {"aged receivables", "aged_receivables", "receivables aged"}:
        return "Aged Receivables"
    if n in {"aged payables", "aged_payables", "payables aged"}:
        return "Aged Payables"
    if n in {"tax report", "tax_report"}:
        return "Tax Report"
    return name


def _flatten_report_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recursively flattens Odoo nested report lines into a simple list."""
    flat = []
    for line in lines:
        # Extract columns
        cols = line.get("columns", [])
        value = 0.0
        formatted_value = ""
        if cols:
            first_col = cols[0]
            if isinstance(first_col, dict):
                value = float(first_col.get("no_format_name", 0.0) or 0.0)
                formatted_value = str(first_col.get("name", "") or "")
        
        flat.append({
            "id": line.get("id"),
            "name": line.get("name", ""),
            "code": line.get("code", ""),
            "level": line.get("level", 0),
            "value": value,
            "formatted_value": formatted_value,
        })
        # If there are sub-lines, recursively flatten them
        if "lines" in line and isinstance(line["lines"], list):
            flat.extend(_flatten_report_lines(line["lines"]))
        if "children" in line and isinstance(line["children"], list):
            flat.extend(_flatten_report_lines(line["children"]))
    return flat


@router.post("/execute")
async def execute_report(req: OdooExecuteReportRequest, auth: dict = Depends(internal_api_key_auth)):
    client = _get_client(req.credentials)
    report_name = _map_report_name(req.report_name)
    report_id = req.report_id
    
    # Try calling the official account.report / account.financial.report model
    try:
        if not report_id:
            # 1. Search by exact mapped name
            report_ids = client.call_with_transport(
                "account.report", 
                "search", 
                [[["name", "=", report_name]]]
            )
            # 2. Search by fuzzy name if exact search returned empty
            if not report_ids:
                report_ids = client.call_with_transport(
                    "account.report", 
                    "search", 
                    [[["name", "ilike", report_name]]]
                )
            
            if not report_ids:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "report_not_found",
                        "message": f"No official Odoo report matching name '{report_name}' was found in the database.",
                        "attempted_report_name": report_name
                    }
                )
            if len(report_ids) > 1:
                # Resolve by picking the closest or first, but warn/log
                pass
            report_id = report_ids[0]

        # Build options payload
        options = {}
        if req.date_from:
            options["date"] = {"date_from": req.date_from, "date_to": req.date_to, "filter": "custom"}
        if req.company_id:
            options["company_id"] = req.company_id
            
        # Call Odoo's report engine
        try:
            report_info = client.call_with_transport(
                "account.report", 
                "get_report_informations", 
                [report_id, options]
            )
        except Exception:
            # fallback to legacy report_get_lines or get_lines
            report_info = client.call_with_transport(
                "account.report", 
                "get_lines", 
                [report_id, options]
            )
            if isinstance(report_info, list):
                report_info = {"lines": report_info}

        lines = report_info.get("lines", [])
        flat_lines = _flatten_report_lines(lines)
        available_line_names = [line["name"] for line in flat_lines if line.get("name")]
        
        # Filter by line names if requested
        filtered_lines = flat_lines
        missing_line_names = []
        if req.line_names:
            filtered_lines = []
            for ln in req.line_names:
                ln_lower = ln.lower()
                matched = [l for l in flat_lines if ln_lower in str(l["name"]).lower()]
                if matched:
                    filtered_lines.extend(matched)
                else:
                    missing_line_names.append(ln)

        currency_code = req.credentials.db[:3].upper() if len(req.credentials.db) >= 3 else "USD"
        if "options" in report_info and isinstance(report_info["options"], dict):
            # Try parsing from options context
            pass
        symbols = {"USD": "$", "ZAR": "R", "EUR": "€", "GBP": "£"}
        currency_symbol = symbols.get(currency_code, currency_code)

        response_payload = {
            "report_name": report_name,
            "report_id": report_id,
            "date_from": req.date_from,
            "date_to": req.date_to,
            "currency_code": currency_code,
            "currency_symbol": currency_symbol,
            "source": "odoo_account_report",
            "line_count": len(filtered_lines),
            "available_line_names": list(set(available_line_names)),
            "missing_line_names": missing_line_names,
            "lines": filtered_lines,
        }
        if req.include_raw_lines:
            response_payload["raw_lines"] = lines
        return response_payload

    except Exception as e:
        # If the requested report is Profit and Loss, drop back to our robust customer invoice fallback!
        if report_name == "Profit and Loss":
            # Execute robust posted invoice fallback
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
                currency_code = "USD"
                if moves:
                    currency_val = moves[0].get("currency_id")
                    if isinstance(currency_val, list) and len(currency_val) == 2:
                        currency_code = currency_val[1]
                    elif isinstance(currency_val, dict) and "name" in currency_val:
                        currency_code = currency_val["name"]
                        
                symbols = {"USD": "$", "ZAR": "R", "EUR": "€", "GBP": "£"}
                currency_symbol = symbols.get(currency_code, currency_code)
                
                # Format to lines structure for compatibility
                fallback_lines = [{
                    "id": "revenue_total",
                    "name": "Total Revenue",
                    "code": "REV",
                    "level": 0,
                    "value": total_revenue,
                    "formatted_value": f"{currency_symbol} {total_revenue:,.2f}"
                }]
                
                return {
                    "report_name": "Profit and Loss (Fallback)",
                    "report_id": None,
                    "date_from": req.date_from,
                    "date_to": req.date_to,
                    "currency_code": currency_code,
                    "currency_symbol": currency_symbol,
                    "source": "fallback_posted_customer_invoices",
                    "warning": "Could not access official Odoo P&L report. Revenue was calculated from posted customer invoices for the selected period.",
                    "line_count": 1,
                    "available_line_names": ["Total Revenue"],
                    "missing_line_names": [],
                    "lines": fallback_lines
                }
            except Exception as fallback_err:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "report_unsupported",
                        "message": f"Both official P&L report and posted customer invoice fallback queries failed. Fallback error: {str(fallback_err)}",
                        "method_attempted": "account.report / account.move / account.invoice"
                    }
                )
        
        # For other reports, raise structured report_unavailable error
        raise HTTPException(
            status_code=400,
            detail={
                "error": "report_unavailable",
                "message": f"Could not execute Odoo account report '{report_name}'. Error: {str(e)}",
                "attempted_report_name": report_name,
                "attempted_report_id": report_id,
                "attempted_model": "account.report",
                "attempted_methods": ["get_report_informations", "get_lines"],
                "likely_causes": [
                    "Missing Accounting access rights",
                    "Odoo Community vs Enterprise edition/version mismatch",
                    "The specific account report module is not installed",
                    "Options payload not supported by this Odoo version"
                ]
            }
        )


@router.post("/profit-and-loss")
async def get_profit_and_loss(req: OdooProfitAndLossRequest, auth: dict = Depends(internal_api_key_auth)):
    """Thin backward compatibility alias that delegates directly to the generic execute_report layer."""
    exec_req = OdooExecuteReportRequest(
        credentials=req.credentials,
        report_name="Profit and Loss",
        date_from=req.date_from,
        date_to=req.date_to,
        company_id=req.company_id,
        line_names=["Revenue"]
    )
    result = await execute_report(exec_req, auth)
    
    # Map back to old P&L-specific schema
    lines = result.get("lines", [])
    total_rev_val = 0.0
    total_rev_formatted = f"{result['currency_symbol']} 0.00"
    for l in lines:
        if "revenue" in str(l["name"]).lower() or "Total Revenue" in str(l["name"]):
            total_rev_val = l["value"]
            total_rev_formatted = l["formatted_value"]
            break
    if not total_rev_val and lines:
        total_rev_val = lines[0]["value"]
        total_rev_formatted = lines[0]["formatted_value"]
        
    pnl_res = {
        "report": result["report_name"],
        "date_from": result["date_from"],
        "date_to": result["date_to"],
        "currency_code": result["currency_code"],
        "currency_symbol": result["currency_symbol"],
        "revenue": {
            "value": total_rev_val,
            "formatted": total_rev_formatted,
            "source": result["source"]
        },
        "lines": lines
    }
    if "warning" in result.get("revenue", {}):
        pnl_res["revenue"]["warning"] = result["revenue"]["warning"]
    return pnl_res
