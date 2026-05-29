import base64
import re
from typing import Any


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

        # Direct model fields
        model = field_meta.get("relation", "")
        if model and field_name in ("product_id", "product_tmpl_id", "partner_id", "customer_id", "vendor_id", "company_id"):
            if isinstance(value, int):
                # Already resolved name is in display_name or name
                name = record.get("display_name") or record.get("name", "")
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


def format_search_read_response(
    model: str,
    records: list[dict[str, Any]],
    fields_info: dict[str, Any] | None = None,
    include_human_references: bool = True,
) -> dict[str, Any]:
    """Format a search_read response with structured metadata."""
    formatted_records = []
    for record in records:
        rec = dict(record)
        rec["__model"] = model
        if include_human_references and fields_info:
            rec = enrich_record_with_human_references(rec, fields_info)
        formatted_records.append(rec)

    return {
        "model": model,
        "count": len(formatted_records),
        "records": formatted_records,
    }


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
