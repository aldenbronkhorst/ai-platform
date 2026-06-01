import logging
from typing import Optional, Any, List, Dict
from fastapi import HTTPException, status
from app.core.odoo_client import OdooClient
from app.models.schemas import OdooExecuteReportRequest

logger = logging.getLogger(__name__)


def _extract_report_id(record: dict[str, Any], index: int = 0) -> int:
    """Safely extract numeric report ID from an account.report search result.
    Raises HTTPException with structured error if id is missing or invalid."""
    rid = record.get("id")
    if rid is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "report_resolution_invalid_shape",
                "message": "Odoo returned an account.report record without an id field. "
                           "This usually means the search was performed without include_ids=True.",
                "record_index": index,
                "record_keys": list(record.keys()),
                "record_sample": {k: record.get(k) for k in list(record.keys())[:5]},
                "likely_cause": "search_read called without include_ids=True, which strips id fields",
                "model": "account.report",
            },
        )
    try:
        return int(rid)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "report_resolution_invalid_shape",
                "message": f"Odoo account.report record has non-numeric id: {rid!r}.",
                "record_index": index,
                "record_id_raw": str(rid),
            },
        )


class OdooReportService:
    def __init__(self, client: OdooClient):
        self.client = client

    def _map_report_name(self, name: str) -> str:
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

    def _flatten_lines(self, lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Recursively flattens Odoo nested report lines into a simple list."""
        flat = []
        for line in lines:
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
            if "lines" in line and isinstance(line["lines"], list):
                flat.extend(self._flatten_lines(line["lines"]))
            elif "children" in line and isinstance(line["children"], list):
                flat.extend(self._flatten_lines(line["children"]))
        return flat

    def _resolve_currency(self, company_id: Optional[int], report_info: Dict[str, Any]) -> tuple[Optional[str], Optional[str], str]:
        """Resolves the report currency. Never guesses from DB name or defaults to USD.
        
        Returns:
            (currency_code, currency_symbol, currency_source)
        """
        # 1. Try parsing from report info / options first
        options = report_info.get("options", {})
        if options and isinstance(options, dict):
            curr = options.get("currency")
            if isinstance(curr, dict):
                code = curr.get("code")
                symbol = curr.get("symbol")
                if code:
                    return code, symbol, "odoo_report_options"

        # 2. Query company currency
        if company_id:
            try:
                companies = self.client.search_read(
                    model="res.company",
                    domain=[("id", "=", company_id)],
                    fields=["currency_id"]
                )
                if companies and companies[0].get("currency_id"):
                    currency_val = companies[0]["currency_id"]
                    if isinstance(currency_val, list) and len(currency_val) == 2:
                        currency_code = currency_val[1]
                        
                        # Query symbol
                        currencies = self.client.search_read(
                            model="res.currency",
                            domain=[("name", "=", currency_code)],
                            fields=["symbol"]
                        )
                        currency_symbol = currencies[0].get("symbol") if currencies else currency_code
                        return currency_code, currency_symbol, "odoo_company_metadata"
            except Exception as e:
                logger.warning("Failed to query Odoo company currency: %s", e)

        return None, None, "unknown"

    def execute(self, req: OdooExecuteReportRequest) -> Dict[str, Any]:
        report_name = self._map_report_name(req.report_name)
        report_id = req.report_id
        
        try:
            # Step 1. Resolve account.report by ID or name with ambiguity checks
            if not report_id:
                exact_res = self.client.search_read(
                    model="account.report",
                    domain=[["name", "=", report_name]],
                    fields=["id", "name"],
                    include_ids=True,
                ) or []
                logger.info(
                    "Odoo report exact search result | report_name=%s result_count=%d sample=%s",
                    report_name, len(exact_res), exact_res[:3],
                )
                if len(exact_res) == 1:
                    report_id = _extract_report_id(exact_res[0], 0)
                elif len(exact_res) > 1:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "report_ambiguity",
                            "message": f"Multiple reports exactly match the name '{report_name}'. Please specify exact report ID.",
                            "candidates": exact_res,
                            "available_report_names": [r.get("name") for r in exact_res if r.get("name")],
                        }
                    )
                else:
                    fuzzy_res = self.client.search_read(
                        model="account.report",
                        domain=[["name", "ilike", report_name]],
                        fields=["id", "name"],
                        include_ids=True,
                    ) or []
                    logger.info(
                        "Odoo report fuzzy search result | report_name=%s result_count=%d sample=%s",
                        report_name, len(fuzzy_res), fuzzy_res[:5],
                    )
                    if len(fuzzy_res) == 1:
                        report_id = _extract_report_id(fuzzy_res[0], 0)
                    elif len(fuzzy_res) > 1:
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "error": "report_ambiguity",
                                "message": f"Multiple reports partially match the name '{report_name}'. Please specify exact report ID.",
                                "candidates": fuzzy_res,
                                "available_report_names": [r.get("name") for r in fuzzy_res if r.get("name")],
                            }
                        )
                    else:
                        raise HTTPException(
                            status_code=404,
                            detail={
                                "error": "report_not_found",
                                "message": f"No official Odoo report matching name '{report_name}' was found.",
                                "attempted_report_name": report_name,
                                "available_report_names": [r.get("name") for r in exact_res if r.get("name")],
                            }
                        )

            # Step 2. Build previous options
            previous_options = {}
            if req.date_from and req.date_to:
                previous_options["date"] = {
                    "date_from": req.date_from,
                    "date_to": req.date_to,
                    "filter": "custom"
                }
            if req.company_id:
                previous_options["company_id"] = req.company_id

            # Step 3. Call get_options
            options = self.client.call_with_transport(
                "account.report",
                "get_options",
                [report_id, previous_options]
            )

            # Step 4. Call get_report_information
            report_info = self.client.call_with_transport(
                "account.report",
                "get_report_information",
                [report_id, options]
            )

            lines = report_info.get("lines", [])
            flat_lines = self._flatten_lines(lines)
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

            # Step 5. Resolve currency
            currency_code, currency_symbol, currency_source = self._resolve_currency(req.company_id, report_info)

            response_payload = {
                "report_name": report_name,
                "report_id": report_id,
                "date_from": req.date_from,
                "date_to": req.date_to,
                "currency_code": currency_code,
                "currency_symbol": currency_symbol,
                "currency_source": currency_source,
                "source": "odoo_account_report",
                "line_count": len(filtered_lines),
                "available_line_names": list(set(available_line_names)),
                "missing_line_names": missing_line_names,
                "lines": filtered_lines,
            }
            if req.include_raw_lines:
                response_payload["raw_lines"] = lines
            return response_payload

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "report_unavailable",
                    "message": f"Could not execute Odoo account report '{report_name}'. Technical error: {str(e)}",
                    "attempted_report_name": report_name,
                    "attempted_report_id": report_id,
                    "attempted_model": "account.report",
                    "attempted_methods": ["get_options", "get_report_information"],
                    "likely_causes": [
                        "Missing Accounting access rights",
                        "Odoo Community vs Enterprise edition/version mismatch",
                        "The specific account report module is not installed",
                        "Options payload not supported by this Odoo version"
                    ]
                }
            )
