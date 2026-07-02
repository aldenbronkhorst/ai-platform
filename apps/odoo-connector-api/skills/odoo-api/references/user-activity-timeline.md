# User Activity Timeline - Working Reference

Query all of a user's activity across Odoo models for a given day, building a chronological timeline with dedup.

## Approach

1. Find the user (`res.users`) by `login ilike`, fallback to `name ilike`.
2. Define the local day in the user's timezone, convert to UTC for Odoo datetime fields.
3. For each model of interest, query `search_read` with:
   - `create_uid = user_id AND create_date >= utc_start AND create_date < utc_end`
   - `write_uid = user_id AND write_date >= utc_start AND write_date < utc_end`
4. Also check `mail.message` (chatter messages, internal notes) by `author_id`.
5. Also check `ir.attachment` (file uploads) by `create_uid`.
6. Collect into a list of `(timestamp, model, record_id, display_name, action)` tuples.
7. Dedup: if a record was created and updated within the same 2-second window, keep only the "Created" entry.
8. Sort by timestamp and present chronologically.

## Models to Scan

**Accounting:** `account.move`, `account.move.line`, `account.payment`, `account.bank.statement.line`
**Sales/Purchasing:** `sale.order`, `sale.order.line`, `purchase.order`, `purchase.order.line`
**Inventory:** `stock.picking`, `stock.move`
**CRM/Partners:** `res.partner`, `product.product`
**HR:** `hr.expense`, `hr.expense.sheet`
**Attachments:** `ir.attachment`
**Chatter:** `mail.message`

## Timezone Handling

```python
from datetime import datetime, timezone, timedelta

local_tz = timezone(timedelta(hours=2))  # e.g. SAST

def to_local(dt_str):
    if not dt_str:
        return None
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(local_tz)

day_start = datetime(2026, 6, 29, 0, 0, 0, tzinfo=local_tz)
day_end = datetime(2026, 6, 30, 0, 0, 0, tzinfo=local_tz)
utc_start = day_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
utc_end = day_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
```

## Safe Helpers

```python
def safe_m2o(v):
    if not v or v is False:
        return "N/A"
    return v[1] if isinstance(v, list) and len(v) > 1 else str(v)
```

## Pitfalls

- Same-second create/update: dedup by checking if a Created entry exists within 2 seconds of an Updated entry for the same model and id.
- `account.move.line` has no `display_name`: use `name` and resolve `move_id` manually.
- `mail.message.body` is HTML: strip tags with `re.sub(r'<[^>]+>', '', body)`.
- `mail.message.subject` can be null: use `"(no subject)"`.
- `ir.attachment.res_model` gives the linked model context.
- Always set `limit` and `order`.
- Wrap per-model scans in try/except because models or fields may not exist or may be inaccessible.
