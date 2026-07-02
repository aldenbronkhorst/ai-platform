---
name: document-reader
description: "Read uploaded PDFs/images through native text extraction or Azure Document Intelligence OCR/layout."
version: 1.0.0
metadata:
  ai_platform:
    tool: document_reader
    broker_target: document_reader
---

# Document Reader

Document Reader is the AI Platform file-reading tool for uploaded PDFs and images. It owns document-specific guidance. Workspace is only the execution environment; Workspace code should call this tool through `call("document_reader", ...)` when it needs uploaded file text or tables.

## Tool Shape

Use the uploaded artifact id from the file context.

```python
tables = call("document_reader", {
    "artifact_id": artifact_id,
    "mode": "tables",
})
```

Modes:

- `guidance`: return this tool-owned skill text.
- `status`: return extraction status and metadata.
- `read`: read line-numbered text with `offset` and `limit`.
- `preview`: return a short text preview.
- `extract`: return extracted text without line pagination.
- `tables`: return structured tables with rows, cells, markdown, and pagination.
- `layout`: return page lines, geometry, and table summaries.

## OCR Profile Selection

The tool selects the Azure OCR profile from the requested mode:

- Use `read`, `preview`, or `extract` for ordinary text. These use native PDF text first; scanned PDFs/images use Azure `prebuilt-read`.
- Use `tables` or `layout` for invoices, GRVs, statements, price lists, purchase orders, bills, sales orders, credit notes, and any document where rows, columns, quantities, prices, or product codes matter. These use Azure `prebuilt-layout`.
- Use `status` before repeating extraction if you need to see whether layout metadata already exists.

Do not parse dense tables from raw text when structured tables are available. For reconciliation, price checks, code checks, or line-item comparisons, use `tables` first, then validate suspicious rows with `layout`/`read` and the connected system data.

## Accuracy Rules

- Treat OCR output as evidence to validate, not as final truth.
- Preserve source file name, page/table index, product code, quantity, unit price, and line total in comparisons.
- For accounting comparisons, compare parsed PDF rows against the system of record instead of relying on OCR totals alone.
- Flag probable OCR code mistakes instead of silently correcting them. Example: if Odoo has `022173` and OCR returned `002173`, report it as a likely OCR/code issue.
- When exact values matter, include counts for matched rows, price discrepancies, PDF-only rows, and system-only rows.

## Workspace Usage

Workspace Python can call Document Reader directly:

```python
payload = call("document_reader", {
    "artifact_id": artifact_id,
    "mode": "tables",
    "table_offset": 1,
    "table_limit": 20,
})

for table in payload["tables"]:
    print(table["table_index"], table["row_count"], table["markdown"])
```

For large documents, page through results instead of asking for everything in one call.
