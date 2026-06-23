import logging
from typing import Optional, Any, List, Dict
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, status
from app.core.odoo_client import OdooClient
from app.models.schemas import OdooExecuteReportRequest

logger = logging.getLogger(__name__)

DEFAULT_DRILLDOWN_LIMIT = 1000
MAX_DRILLDOWN_LIMIT = 5000
DEFAULT_DRILLDOWN_FIELDS_BY_MODEL = {
    "account.move.line": [
        "date",
        "move_id",
        "journal_id",
        "account_id",
        "partner_id",
        "name",
        "debit",
        "credit",
        "balance",
        "amount_currency",
        "currency_id",
        "tax_ids",
        "tax_line_id",
        "tax_tag_ids",
        "parent_state",
    ],
}


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

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _bounded_limit(limit: int | None) -> int:
        try:
            parsed = int(limit or DEFAULT_DRILLDOWN_LIMIT)
        except (TypeError, ValueError):
            parsed = DEFAULT_DRILLDOWN_LIMIT
        return max(1, min(parsed, MAX_DRILLDOWN_LIMIT))

    @staticmethod
    def _bounded_offset(offset: int | None) -> int:
        try:
            parsed = int(offset or 0)
        except (TypeError, ValueError):
            parsed = 0
        return max(0, parsed)

    @staticmethod
    def _odoo_base_url(req: OdooExecuteReportRequest) -> str:
        raw_url = (req.credentials.url or "").strip().rstrip("/")
        if not raw_url:
            return ""

        parsed = urlsplit(raw_url)
        if not parsed.scheme or not parsed.netloc:
            return raw_url[:-4] if raw_url.endswith("/web") else raw_url

        path = parsed.path.rstrip("/")
        if path == "/web":
            path = ""
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    def _record_url(self, req: OdooExecuteReportRequest, model: str | None, record_id: Any) -> str | None:
        if not model or not isinstance(record_id, int) or isinstance(record_id, bool):
            return None
        base_url = self._odoo_base_url(req)
        if not base_url:
            return None
        fragment = urlencode({"id": record_id, "model": model, "view_type": "form"})
        return f"{base_url}/web#{fragment}"

    @classmethod
    def _audit_columns(cls, line: Dict[str, Any]) -> List[Dict[str, Any]]:
        audit_columns: List[Dict[str, Any]] = []
        for index, column in enumerate(line.get("columns") or []):
            if not isinstance(column, dict):
                continue
            if not (column.get("auditable") or column.get("has_sublines")):
                continue
            audit_columns.append(
                {
                    "column_index": index,
                    "formatted_value": str(column.get("name", "") or ""),
                    "value": cls._safe_float(column.get("no_format", column.get("no_format_name", 0.0))),
                    "figure_type": column.get("figure_type"),
                    "report_line_id": column.get("report_line_id"),
                    "expression_label": column.get("expression_label"),
                    "column_group_key": column.get("column_group_key"),
                    "auditable": bool(column.get("auditable")),
                    "has_sublines": bool(column.get("has_sublines")),
                }
            )
        return audit_columns

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
                    value = self._safe_float(first_col.get("no_format", first_col.get("no_format_name", 0.0)))
                    formatted_value = str(first_col.get("name", "") or "")
            audit_columns = self._audit_columns(line)
            flat.append({
                "id": line.get("id"),
                "name": line.get("name", ""),
                "code": line.get("code", ""),
                "level": line.get("level", 0),
                "value": value,
                "formatted_value": formatted_value,
                "drilldown_available": bool(audit_columns),
                "drilldown_columns": audit_columns,
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

    def _report_options(self, report_id: int, req: OdooExecuteReportRequest) -> Dict[str, Any]:
        return self.client.call_with_transport(
            "account.report",
            "get_options",
            [report_id, self._previous_options(req)]
        )

    def _report_information(self, report_id: int, options: Dict[str, Any]) -> Dict[str, Any]:
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

    def _drilldown_fields_for_model(self, model: str, requested_fields: Optional[List[str]]) -> List[str]:
        requested = requested_fields or DEFAULT_DRILLDOWN_FIELDS_BY_MODEL.get(model) or ["display_name"]
        fields_info = self.client.fields_get(model, fields=requested, attributes=["string", "type"]).get("fields", {})
        return [field for field in requested if field in fields_info] or ["display_name"]

    def _read_drilldown_records(
        self,
        *,
        req: OdooExecuteReportRequest,
        action: Dict[str, Any],
    ) -> Dict[str, Any]:
        model = action.get("res_model")
        domain = action.get("domain")
        if not model or not isinstance(domain, list):
            return {
                "record_source": "odoo_report_action",
                "records": [],
                "returned_count": 0,
                "total_count": None,
                "complete": True,
                "has_more": False,
            }

        limit = self._bounded_limit(req.drilldown_limit)
        offset = self._bounded_offset(req.drilldown_offset)
        fields = self._drilldown_fields_for_model(model, req.drilldown_fields)
        order = "date asc, id asc" if model == "account.move.line" and "date" in fields else "id asc"
        total_count = self.client.search_count(model=model, domain=domain)
        records = self.client.search_read(
            model=model,
            domain=domain,
            fields=fields,
            limit=limit,
            offset=offset,
            order=order,
            include_ids=True,
        )
        for record in records:
            if isinstance(record, dict):
                record["record_url"] = self._record_url(req, model, record.get("id"))
        returned_count = len(records)
        return {
            "record_source": "odoo_report_action",
            "res_model": model,
            "domain": domain,
            "fields": fields,
            "limit": limit,
            "offset": offset,
            "records": records,
            "returned_count": returned_count,
            "total_count": total_count,
            "complete": offset + returned_count >= total_count,
            "has_more": offset + returned_count < total_count,
        }

    def _drilldowns(
        self,
        *,
        req: OdooExecuteReportRequest,
        report_id: int,
        report_options: Dict[str, Any],
        lines: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        drilldowns: List[Dict[str, Any]] = []
        for line in lines:
            for column in line.get("drilldown_columns") or []:
                params = {
                    "report_line_id": column.get("report_line_id"),
                    "expression_label": column.get("expression_label"),
                    "calling_line_dict_id": line.get("id"),
                    "column_group_key": column.get("column_group_key"),
                }
                if not all(params.values()):
                    continue
                drilldown: Dict[str, Any] = {
                    "line_id": line.get("id"),
                    "line_name": line.get("name"),
                    "column_index": column.get("column_index"),
                    "formatted_value": column.get("formatted_value"),
                    "value": column.get("value"),
                    "source": "odoo_account_report_action_audit_cell",
                }
                try:
                    action = self.client.call_with_transport(
                        "account.report",
                        "dispatch_report_action",
                        [report_id, report_options, "action_audit_cell", params],
                    )
                    drilldown["action"] = action
                    if isinstance(action, dict):
                        drilldown.update(self._read_drilldown_records(req=req, action=action))
                except Exception as exc:
                    drilldown.update(
                        {
                            "error": True,
                            "error_type": "report_drilldown_unavailable",
                            "message": str(exc),
                        }
                    )
                drilldowns.append(drilldown)
        return drilldowns

    def _response_payload(
        self,
        *,
        req: OdooExecuteReportRequest,
        report_name: str,
        report_id: int,
        report_options: Dict[str, Any],
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
        if req.include_drilldowns:
            response_payload["drilldowns"] = self._drilldowns(
                req=req,
                report_id=report_id,
                report_options=report_options,
                lines=filtered_lines,
            )
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
            report_options = self._report_options(resolved_report_id, req)
            report_info = self._report_information(resolved_report_id, report_options)
            return self._response_payload(
                req=req,
                report_name=report_name,
                report_id=resolved_report_id,
                report_options=report_options,
                report_info=report_info,
            )

        except HTTPException:
            raise
        except Exception as e:
            self._raise_report_unavailable(report_name, report_id, e)
