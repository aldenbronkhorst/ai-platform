import base64
import re
from typing import Any, Optional


def enrich_record_with_human_references(record: dict[str, Any], fields_info: dict[str, Any]) -> dict[str, Any]:
    """Add _human_reference fields to records based on Odoo field metadata.

    Products: [default_code] name
    Contacts: [ref] name
    Documents: name + partner reference
    """
    enriched = dict(record)

    for field_name, value in list(record.items()):
        if field_name.endswith("_human_reference"):
            continue

        field_meta = fields_info.get(field_name, {})
        relation = field_meta.get("relation", "")

        # Many2one relation fields
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int) and isinstance(value[1], str):
            ref = _human_reference_for_model(relation, value[0], value[1])
            if ref:
                enriched[f"{field_name}_human_reference"] = ref

        # Direct model fields (Many2one stored as integer ID with name in sibling field)
        model = field_meta.get("relation", "")
        if model and field_name in ("product_id", "product_tmpl_id", "partner_id", "customer_id", "vendor_id", "company_id"):
            if isinstance(value, int):
                name_field = f"{field_name}_name"
                name = record.get(name_field) or record.get("display_name") or record.get("name", "")
                ref = _human_reference_for_model(model, value, name)
                if ref:
                    enriched[f"{field_name}_human_reference"] = ref

    # Add top-level human reference for the record itself
    model = record.get("__model")
    if model:
        ref = _human_reference_for_model(model, record.get("id"), record.get("display_name") or record.get("name", ""))
        if ref:
            enriched["_human_reference"] = ref

    return enriched


def _human_reference_for_model(model: str, record_id: int | None, name: str) -> dict[str, Any] | None:
    if record_id is None:
        return None

    label = name or ""

    # Product-style: extract default_code if present in label
    if model in ("product.product", "product.template"):
        code_match = re.match(r"\[([^\]]+)\]\s*(.*)", label)
        if code_match:
            return {"id": record_id, "label": label, "code": code_match.group(1), "name": code_match.group(2).strip()}
        return {"id": record_id, "label": label, "code": None, "name": label}

    # Contact-style: extract ref if present
    if model in ("res.partner", "res.company"):
        ref_match = re.match(r"\[([^\]]+)\]\s*(.*)", label)
        if ref_match:
            return {"id": record_id, "label": label, "ref": ref_match.group(1), "name": ref_match.group(2).strip()}
        return {"id": record_id, "label": label, "ref": None, "name": label}

    # Document-style: use name + ID
    if model in ("account.move", "sale.order", "purchase.order", "stock.picking"):
        return {"id": record_id, "label": label, "document_number": label}

    # Generic
    return {"id": record_id, "label": label}


def format_mutation_response(
    model: str,
    operation: str,
    result: Any,
    record_ids: list[int],
    verified_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "model": model,
        "operation": operation,
        "record_ids": record_ids,
        "result_summary": _summarize_result(result),
        "verified_records": verified_records or [],
    }


def _summarize_result(result: Any) -> dict[str, Any]:
    if isinstance(result, bool):
        return {"success": result}
    if isinstance(result, int):
        return {"created_id": result}
    if isinstance(result, list) and all(isinstance(x, int) for x in result):
        return {"created_ids": result, "count": len(result)}
    return {"raw": result}


def extract_text_from_attachment(record: dict[str, Any], mode: str = "auto") -> dict[str, Any]:
    """Basic text extraction from attachment record.

    Modes: metadata, base64, text, auto
    """
    output = {
        "id": record.get("id"),
        "name": record.get("name"),
        "mimetype": record.get("mimetype"),
        "size": record.get("file_size"),
    }

    if mode == "metadata":
        return output

    raw_b64 = record.get("datas")
    data = base64.b64decode(raw_b64) if isinstance(raw_b64, str) else b""

    if mode == "base64":
        output["content_base64"] = base64.b64encode(data).decode() if data else ""
        return output

    text = ""
    text_source = "none"
    mimetype = str(record.get("mimetype") or "")

    # Try index_content first (Odoo's built-in text extraction)
    if record.get("index_content"):
        text = str(record.get("index_content") or "")
        text_source = "index_content"
    elif "pdf" in mimetype and data:
        # Basic text extraction would need PyPDF2 or similar
        # For now, indicate it's available but not extracted
        text_source = "pdf_needs_ocr"

    output["text"] = text
    output["text_source"] = text_source
    output["text_length"] = len(text)

    return output


def format_attachment_response(record: dict[str, Any], mode: str = "auto") -> dict[str, Any]:
    return extract_text_from_attachment(record, mode)


MONEY_FIELD_SUFFIXES = (
    "_total", "_residual", "_amount", "_untaxed", "_tax",
    "amount_total", "amount_residual", "amount_untaxed", "amount_tax",
    "price_total", "price_subtotal", "price_unit",
    "balance", "debit", "credit", "amount_currency",
)


def _is_money_field(field_name: str) -> bool:
    """Check if a field name looks like a financial/monetary amount."""
    lower = field_name.lower()
    for suffix in MONEY_FIELD_SUFFIXES:
        if lower == suffix or lower.endswith(suffix):
            return True
    if lower.startswith(("amount_", "price_", "total_")):
        return True
    return False


def _format_money_value(value: Any, currency_code: str = "ZAR", currency_symbol: str = "R") -> dict:
    """Format a raw numeric value into a structured money object.

    Args:
        value: The raw numeric value.
        currency_code: ISO currency code (e.g. ZAR, USD, EUR).
        currency_symbol: Currency symbol (e.g. R, $, €).

    Returns:
        A dict with value, currency_code, currency_symbol, formatted, and source.
    """
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None

    # Format with thousand separators and 2 decimal places
    formatted = f"{currency_symbol} {num:,.2f}".replace(",", " ").replace(".", ",") if currency_symbol == "R" else f"{currency_symbol}{num:,.2f}"

    return {
        "value": num,
        "currency_code": currency_code,
        "currency_symbol": currency_symbol,
        "formatted": formatted,
        "source": "odoo.money_field",
    }


def _normalize_zar_format(num: float, currency_symbol: str) -> str:
    """Format ZAR amounts in South African notation: R 1,234.56"""
    return f"{currency_symbol} {num:,.2f}"


def normalize_money_values(
    record: dict[str, Any],
    currency_cache: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Scan a record for money fields and add _money metadata.

    Builds a money-formatted parallel value for each detected financial field.
    If currency_cache contains 'code' and 'symbol', those are preferred.
    Otherwise falls back to the record's own currency_id or company_currency_id.
    """
    enriched = dict(record)

    # Resolve currency from cache or record
    if currency_cache:
        currency_code = currency_cache.get("code", "ZAR")
        currency_symbol = currency_cache.get("symbol", "R")
    else:
        currency_code = "ZAR"
        currency_symbol = "R"
        # Try to extract from record fields
        currency_id = record.get("currency_id")
        if isinstance(currency_id, list) and len(currency_id) >= 2:
            currency_code = str(currency_id[1]) if currency_id[1] else "ZAR"
        company_currency = record.get("company_currency_id")
        if isinstance(company_currency, list) and len(company_currency) >= 2:
            currency_code = str(company_currency[1]) or currency_code

    for field_name, value in list(record.items()):
        if field_name.endswith("_money") or field_name.startswith("_"):
            continue
        if not _is_money_field(field_name):
            continue
        money_value = _format_money_value(value, currency_code, currency_symbol)
        if money_value:
            enriched[f"{field_name}_money"] = money_value

    return enriched


def format_search_read_response(
    model: str,
    records: list[dict[str, Any]],
    fields_info: dict[str, Any] | None = None,
    include_human_references: bool = True,
    currency_code: Optional[str] = None,
    currency_symbol: Optional[str] = None,
) -> dict[str, Any]:
    """Format a search_read response with structured metadata and money normalization."""
    formatted_records = []
    currency_cache = None
    if currency_code and currency_symbol:
        currency_cache = {"code": currency_code, "symbol": currency_symbol}

    for record in records:
        rec = dict(record)
        rec["__model"] = model
        if include_human_references and fields_info:
            rec = enrich_record_with_human_references(rec, fields_info)
        # Normalize money values
        rec = normalize_money_values(rec, currency_cache)
        formatted_records.append(rec)

    return {
        "model": model,
        "count": len(formatted_records),
        "records": formatted_records,
    }


def format_message_response(record: dict[str, Any]) -> dict[str, Any]:
    """Format a mail.message record for clean consumption."""
    body = record.get("body", "")
    # Strip HTML tags for plain text preview
    plain_text = re.sub(r"<[^>]+>", " ", body)
    plain_text = re.sub(r"\s+", " ", plain_text).strip()

    return {
        "id": record.get("id"),
        "date": record.get("date"),
        "author": record.get("author_id")[1] if isinstance(record.get("author_id"), list) else None,
        "subject": record.get("subject"),
        "body_preview": plain_text[:500],
        "body_truncated": len(plain_text) > 500,
        "message_type": record.get("message_type"),
    }
