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

    @staticmethod
    def _raise_report_ambiguity(report_name: str, candidates: List[Dict[str, Any]], match_type: str) -> None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "report_ambiguity",
                "message": f"Multiple reports {match_type} match the name '{report_name}'. Please specify exact report ID.",
                "candidates": candidates,
                "available_report_names": [report.get("name") for report in candidates if report.get("name")],
            }
        )

    @staticmethod
    def _raise_report_not_found(report_name: str, exact_res: List[Dict[str, Any]]) -> None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "report_not_found",
                "message": f"No official Odoo report matching name '{report_name}' was found.",
                "attempted_report_name": report_name,
                "available_report_names": [report.get("name") for report in exact_res if report.get("name")],
            }
        )

    def _search_reports(self, report_name: str, operator: str) -> List[Dict[str, Any]]:
        result = self.client.search_read(
            model="account.report",
            domain=[["name", operator, report_name]],
            fields=["id", "name"],
            include_ids=True,
        ) or []
        logger.info(
            "Odoo report %s search result | report_name=%s result_count=%d sample=%s",
            "exact" if operator == "=" else "fuzzy",
            report_name,
            len(result),
            result[:5],
        )
        return result

    def _resolve_report_id(self, report_name: str, report_id: Optional[int]) -> int:
        if report_id:
            return report_id

        exact_res = self._search_reports(report_name, "=")
        if len(exact_res) == 1:
            return _extract_report_id(exact_res[0], 0)
        if len(exact_res) > 1:
            self._raise_report_ambiguity(report_name, exact_res, "exactly")

        fuzzy_res = self._search_reports(report_name, "ilike")
        if len(fuzzy_res) == 1:
            return _extract_report_id(fuzzy_res[0], 0)
        if len(fuzzy_res) > 1:
            self._raise_report_ambiguity(report_name, fuzzy_res, "partially")
        self._raise_report_not_found(report_name, exact_res)

    @staticmethod
    def _previous_options(req: OdooExecuteReportRequest) -> Dict[str, Any]:
        previous_options: Dict[str, Any] = {}
        if req.date_from and req.date_to:
            previous_options["date"] = {
                "date_from": req.date_from,
                "date_to": req.date_to,
                "filter": "custom"
            }
        if req.company_id:
            previous_options["company_id"] = req.company_id
        return previous_options

    def _report_information(self, report_id: int, req: OdooExecuteReportRequest) -> Dict[str, Any]:
        options = self.client.call_with_transport(
            "account.report",
            "get_options",
            [report_id, self._previous_options(req)]
        )
        return self.client.call_with_transport(
            "account.report",
            "get_report_information",
            [report_id, options]
        )

    @staticmethod
    def _filter_lines(
        flat_lines: List[Dict[str, Any]],
        line_names: Optional[List[str]],
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        if not line_names:
            return flat_lines, []

        filtered_lines = []
        missing_line_names = []
        for line_name in line_names:
            line_name_lower = line_name.lower()
            matched = [line for line in flat_lines if line_name_lower in str(line["name"]).lower()]
            if matched:
                filtered_lines.extend(matched)
            else:
                missing_line_names.append(line_name)
        return filtered_lines, missing_line_names

    def _response_payload(
        self,
        *,
        req: OdooExecuteReportRequest,
        report_name: str,
        report_id: int,
        report_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        lines = report_info.get("lines", [])
        flat_lines = self._flatten_lines(lines)
        filtered_lines, missing_line_names = self._filter_lines(flat_lines, req.line_names)
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
            "available_line_names": list({line["name"] for line in flat_lines if line.get("name")}),
            "missing_line_names": missing_line_names,
            "lines": filtered_lines,
        }
        if req.include_raw_lines:
            response_payload["raw_lines"] = lines
        return response_payload

    @staticmethod
    def _raise_report_unavailable(report_name: str, report_id: Optional[int], exc: Exception) -> None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "report_unavailable",
                "message": f"Could not execute Odoo account report '{report_name}'. Technical error: {str(exc)}",
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

    def execute(self, req: OdooExecuteReportRequest) -> Dict[str, Any]:
        report_name = self._map_report_name(req.report_name)
        report_id = req.report_id

        try:
            resolved_report_id = self._resolve_report_id(report_name, report_id)
            report_info = self._report_information(resolved_report_id, req)
            return self._response_payload(
                req=req,
                report_name=report_name,
                report_id=resolved_report_id,
                report_info=report_info,
            )

        except HTTPException:
            raise
        except Exception as e:
            self._raise_report_unavailable(report_name, report_id, e)
