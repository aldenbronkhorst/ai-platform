---
name: odoo-api
description: "Use the AI Platform Odoo connector via JSON-RPC execute_kw."
version: 2.5.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [odoo, erp, api, crm]
  ai_platform:
    connector: odoo
    broker_target: odoo
---

# Odoo API

Direct integration with Odoo ERP via JSON-RPC. **JSON-RPC only — never XML-RPC.** No third-party packages, no pre-programmed scenario tools — just raw model/method calls to Odoo through the connector.

Reference baseline: Odoo 18 External API docs (`developer/reference/external_api.html`). The official examples are written with XML-RPC endpoints, but the broad contract is the same: authenticate, then call model methods through `execute_kw`. In this skill, always map that contract to `/jsonrpc`.

## AI Platform Usage

The Odoo connector owns credentials and this `SKILL.md`. Do not place Odoo-specific instructions in Workspace. Workspace is only the execution environment; call the connector through the broker target `odoo`.

**Workspace state does not carry across runs.** Each Workspace run is a fresh Python process, so variables and imports from a previous run are gone — a later run that references them raises `NameError` (e.g. defining `rows` in one run and using it in the next). Files *do* persist: the session reuses one working directory, so write intermediate data to a file (e.g. `open("scratch.json","w")` or `output_path(...)`) in one run and reload it in the next. For a multi-step analysis, prefer doing the whole thing — fetch, transform, report — in a **single script**; only split across runs if you persist the data to a file and reload it.

In Workspace Python:

```python
result = call("odoo", {
    "model": "res.partner",
    "method": "search_read",
    "args": [[["is_company", "=", True]]],
    "kwargs": {"fields": ["name", "email"], "limit": 10},
})
```

For the connector's own guidance:

```python
guidance_payload = call("odoo", {"operation": "guidance"})
print(guidance_payload["content"])
```

`call("odoo", {"operation": "guidance"})` returns this connector package's metadata and connector-owned `SKILL.md` markdown text.

The connector injects the linked user's Odoo URL, database, username, and API key server-side. Never ask Workspace code to read or print Odoo secrets.

## Authentication and API Keys

Odoo API keys replace the password in API calls; the login/username remains in use. Treat API keys like passwords: they provide account access, cannot be retrieved after generation, and must be revoked/recreated if lost.

The connector verifies reachability and authenticates server-side with Odoo's `common.authenticate`; the returned `uid` is used internally for model calls. Workspace code should not handle Odoo credentials directly.

## `execute_kw` Shape

Examples below show Odoo's underlying `execute_kw` contract. In AI Platform Workspace, send the same `model`, `method`, `args`, and `kwargs` shape to `call("odoo", ...)`; the connector performs the JSON-RPC wrapper and authentication.

All model access goes through `object.execute_kw`:

```python
odoo("object", "execute_kw", [
    db, uid, api_key,
    "model.name",     # model
    "method_name",    # method
    [positional_args], # positional args for that method
    {"kw": "args"},   # optional keyword args
])
```

Keep args flat. Domain and kwargs are separate top-level elements — do not nest them together:

```python
records = odoo("object", "execute_kw", [
    db, uid, api_key, "res.partner", "search_read",
    [[["is_company", "=", True]]],
    {"fields": ["name", "email"], "limit": 10},
])
```

## Official External API Operations

Use these broad primitives instead of building scenario-specific tools.

```python
# List IDs with a domain. Add offset/limit/order for pagination.
ids = odoo("object", "execute_kw", [
    db, uid, api_key, "res.partner", "search",
    [[["is_company", "=", True]]],
    {"offset": 0, "limit": 100, "order": "name asc"},
])

# Count without fetching all IDs. Do not assume search + search_count are atomic if data changes between calls.
count = odoo("object", "execute_kw", [
    db, uid, api_key, "res.partner", "search_count",
    [[["is_company", "=", True]]],
])

# Read selected fields only. Reading all fields can be very large.
rows = odoo("object", "execute_kw", [
    db, uid, api_key, "res.partner", "read",
    [ids],
    {"fields": ["name", "email"]},
])

# Search and read in one request. Equivalent to search() + read().
rows = odoo("object", "execute_kw", [
    db, uid, api_key, "res.partner", "search_read",
    [[["is_company", "=", True]]],
    {"fields": ["name", "email"], "limit": 100},
])

# Inspect fields before guessing names/types.
fields = odoo("object", "execute_kw", [
    db, uid, api_key, "res.partner", "fields_get",
    [],
    {"attributes": ["string", "help", "type"]},
])

# Create / update / delete.
new_id = odoo("object", "execute_kw", [db, uid, api_key, "res.partner", "create", [{"name": "New Partner"}]])
odoo("object", "execute_kw", [db, uid, api_key, "res.partner", "write", [[new_id], {"name": "Updated Partner"}]])
odoo("object", "execute_kw", [db, uid, api_key, "res.partner", "unlink", [[new_id]]])

# Aggregation when a model supports it.
groups = odoo("object", "execute_kw", [
    db, uid, api_key, "account.move.line", "read_group",
    [[["parent_state", "=", "posted"]], ["balance"], []],
])
```

## Data Shapes and Field Safety

- Empty/unset fields often return `False`, not `None` or `""`.
- Many2one values return `[id, display_name]`; empty Many2one returns `False`.
- `id` is commonly returned even if not requested.
- Date, Datetime, and Binary fields use string values over the API.
- One2many and Many2many writes use Odoo's special command protocol; inspect field types before writing relational values.

```python
def safe_str(v):
    return str(v) if v and v is not False else ""

def safe_m2o(v):
    if not v or v is False:
        return "N/A"
    return v[1] if isinstance(v, list) and len(v) > 1 else str(v)

def safe_amount(v):
    return float(v) if v and v is not False else 0.0
```

## Timezone

Use the device's local timezone for human-facing dates/times. Odoo stores datetime fields (`create_date`, `write_date`) in UTC; convert local day boundaries to UTC before filtering. Date fields (`date`, `invoice_date`) are timezone-naive; use plain date strings.

```python
import time
from datetime import datetime, timezone, timedelta

utc_offset = -time.timezone if not time.daylight else -time.altzone
local_tz = timezone(timedelta(seconds=utc_offset))

def local_day_range(date_str):
    d = datetime.fromisoformat(date_str).replace(tzinfo=local_tz)
    end = d + timedelta(days=1)
    return (
        d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )

def to_local(dt_str):
    if not dt_str or dt_str is False:
        return "N/A"
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S")
```

## Currency

Never hardcode currency symbols. Prefer the record's own `currency_id` when present; otherwise fetch company currency:

```python
company = odoo("object", "execute_kw", [
    db, uid, api_key, "res.company", "search_read",
    [[]], {"fields": ["name", "currency_id"], "limit": 1},
])[0]
cur = odoo("object", "execute_kw", [
    db, uid, api_key, "res.currency", "read",
    [[company["currency_id"][0]], ["symbol", "position", "name"]],
])[0]
```

## Discovery Before Action

When a request involves unfamiliar models, fields, or apps, inspect before acting:

- `fields_get` for available fields and types on a known model.
- `ir.model` to discover installed model names and descriptions.
- `ir.model.fields` to inspect field metadata across models.
- `search_read` with small `limit` and explicit `fields` to sample records.

Avoid guessing technical field names when `fields_get` can confirm them.

## Financial Reports

**Primary method: use Odoo's own `account.report` report engine.** Do not make formula replication the default. For P&L, Balance Sheet, drilldowns, etc., ask Odoo for the report output and read values from returned report lines.

Verified Odoo 18 flow:

1. Find the report with `account.report.search_read`.
2. Call public `account.report.get_options` with the desired lightweight date/options. This expands Odoo's full internal option structure (`currency_table`, `columns`, `column_groups`, companies, etc.).
3. Pass those returned options unchanged into `account.report.get_report_information`.
4. Read returned `lines`; values are in `line["columns"]`.
5. For drilldown/unfold, put the returned report line id in `options["unfolded_lines"]` and call `get_report_information` again.

```python
report = odoo("object", "execute_kw", [
    db, uid, api_key, "account.report", "search_read",
    [[["name", "ilike", "Profit and Loss"]]],
    {"fields": ["id", "name"], "limit": 1},
])[0]

previous_options = {
    "date": {"mode": "range", "date_from": "2026-05-01", "date_to": "2026-05-31", "filter": "custom"},
    "unfolded_lines": [],
    "unfold_all": False,
}
options = odoo("object", "execute_kw", [
    db, uid, api_key, "account.report", "get_options",
    [[report["id"]], previous_options],
])
report_data = odoo("object", "execute_kw", [
    db, uid, api_key, "account.report", "get_report_information",
    [[report["id"]], options],
])
```

Read exact line names and values from `report_data["lines"]`. Use `line["columns"][...]["name"]` for displayed text and `line["columns"][...]["no_format"]` for raw numeric value. For drilldown/unfold, reuse returned line IDs and options; do not invent transactions. Some report lines unfold to child lines first, then to move-line actions/details.

Important: pass the desired date into `get_options` first. If you edit `options["date"]` after `get_options`, internal `column_groups` may still point to the old date. If `get_report_information` fails because of version/customization differences, inspect public methods, field metadata, and/or UI JSON payloads before using formula replication as a fallback.

## Resequencing Journal Entries

**Modern Odoo (v14+) does not use `ir.sequence` for journal entry names (`account.move`).** Instead, they are computed dynamically based on the name of the latest posted entry. To change formatting style (e.g. from `/` formatting like `BNK1/2026/00001` to `-` formatting like `BNK1-2026-00001`) safely, **always use the built-in `account.resequence.wizard`** instead of raw SQL updates or simple ORM `write` calls. This ensures all partner ledgers, tax records, and internal pointers remain audit-sound.

### Resequence Wizard flow:

1. Retrieve the IDs of the journal entries you wish to resequence, sorted in chronological/alphabetical order (usually `date asc, name asc`).
2. Construct the desired `first_name` representing the start of the sequence (e.g., `BNK1-2026-00001`).
3. Create an instance of the `account.resequence.wizard` model using the `move_ids` M2M relation and passing `active_ids` in context.
4. Execute the **`resequence`** method (which represents Odoo's Confirm action button).

```python
ctx = {
    "active_ids": [53990, 53991, 53992],
    "active_model": "account.move"
}
wizard_vals = {
    "first_name": "BNK2-2026-00001",
    "ordering": "keep",
    "move_ids": [(6, 0, [53990, 53991, 53992])]
}

# 1. Create the wizard
wizard_id = odoo("object", "execute_kw", [
    db, uid, api_key, "account.resequence.wizard", "create",
    [wizard_vals],
    {"context": ctx}
])

# 2. Read preview from 'new_values' before confirming
preview_data = odoo("object", "execute_kw", [
    db, uid, api_key, "account.resequence.wizard", "read",
    [[wizard_id]],
    {"fields": ["new_values"]}
])[0]
print(preview_data.get("new_values"))

# 3. Apply resequencing natively 
odoo("object", "execute_kw", [
    db, uid, api_key, "account.resequence.wizard", "resequence",
    [[wizard_id]],
    {"context": ctx}
])
```

## User Activity (Audit Timeline)

For audit-style timelines of what a specific user did on a given day, query across models using `create_uid`/`write_uid` and `create_date`/`write_date`, converting local day boundaries to UTC for datetime fields.

**See also:** `references/user-activity-timeline.md` for a full worked example.

## Resequencing and Rejournaling Posted Entries

When correcting misaligned opening balances or updating name/sequence formatting (such as replacing slashes with dashes or removing month sections), Odoo's strict constraints on posted items require structured ORM steps:

1. **Rejournaling:** Call `button_draft` to revert to draft, write the new `journal_id` (and set `name = "/"` to reset sequence calculation), and then call `action_post` to re-post the move under the new journal.
2. **Resequencing:** For format changes (e.g. `OPN-2026-02-0006` ➔ `OPN-2026-00006`), create an `account.resequence.wizard` record with `first_name` set to the pattern of your oldest record, passing active IDs in the context, and call its `resequence` method.

**See also:** `references/resequence-and-rejournal.md` for complete implementation code and configuration details.

## Resequencing & Journal Transitions

For modifying sequence structures (e.g. changing `/` to `-` in names) or migrating posted journal entries into different journals (such as migrating opening balances from a miscellaneous journal to the true Opening Balances journal):

**See also:** `references/resequencing-and-journal-transitions.md` for full instructions, code snippets, and safety recipes on using the `account.resequence.wizard` and moving posted entries via `button_draft` and `action_post`.

### Step-by-step

1. **Find user** — search `res.users` by `login ilike`, fallback `name ilike`.
2. **Define day range** — convert local day (e.g. SAST 00:00–next 00:00) to UTC bounds.
3. **Scan models** — for each model, query `search_read` twice (created + updated). Wrap each in `try/except` since models may not exist or be accessible.
4. **Include chatter & attachments** — `mail.message` by `author_id`, `ir.attachment` by `create_uid`.
5. **Dedup** — if a record was created and updated within ~2 seconds, keep only the "Created" entry.
6. **Sort & present** — collect `(timestamp, model, record_id, display_name, action)` tuples, sort chronologically.

### Models to Scan

- Accounting: `account.move`, `account.move.line`, `account.payment`, `account.bank.statement.line`
- Sales/Purchasing: `sale.order`, `sale.order.line`, `purchase.order`, `purchase.order.line`
- Inventory: `stock.picking`, `stock.move`
- Partners/Products: `res.partner`, `product.product`
- HR: `hr.expense`, `hr.expense.sheet`
- Chatter & Files: `mail.message` (author_id), `ir.attachment` (create_uid)

### Common Fields Query Pattern

```python
records = odoo("object", "execute_kw", [
    db, uid, api_key, model, "search_read",
    [[["create_uid", "=", user_id], ["create_date", ">=", utc_start], ["create_date", "<", utc_end]]],
    {"fields": ["id", "create_date", "display_name"], "limit": 200, "order": "create_date asc"},
])
```

For models without `display_name` (e.g. `account.move.line`), use `name`, `move_id`, `account_id` fields and resolve via `safe_m2o`.

### Display Tips

- `mail.message.body` is HTML — strip with `re.sub(r'<[^>]+>', '', body)`.
- `mail.message.subject` can be null — use `"(no subject)"` fallback.
- Show attachments with their `res_model` context and `mimetype`.
- Format timeline entries as `[HH:MM:SS] model → Record Name (action)`.

Search users by `login ilike`; if no result, fall back to `name ilike`.

## Common Models

`res.partner`, `sale.order`, `purchase.order`, `account.move`, `account.move.line`, `stock.picking`, `stock.move`, `product.product`, `res.users`, `res.company`, `res.currency`, `mail.message`, `ir.attachment`, `ir.model`, `ir.model.fields`, `account.report`, `account.report.line`, `account.report.expression`.

## Filter Syntax

Domains use `[('field', 'operator', value)]` expressed as JSON lists: `[["field", "operator", value]]`. Operators: `=`, `!=`, `in`, `not in`, `>`, `<`, `>=`, `<=`, `ilike`, `like`, `child_of`. Multiple filters are AND'd. OR uses explicit `"|"`.

## Troubleshooting

When a user reports that something is *wrong* with Odoo (not just "how do I…"), do not improvise.
Run a structured diagnostic loop and fetch the matching playbook on demand.

**Diagnostic discipline (OODA):** Observe with read-only calls → Orient to ranked hypotheses →
Decide the narrowest safe next step → Act → re-read to Confirm. Diagnose read-only first; stop for
explicit user confirmation before any `create` / `write` / `unlink` or posted-entry change. Some
symptoms are code/deployment defects you cannot fix through `execute_kw` — diagnose precisely and
hand off rather than ORM-hacking around them.

**Fetch the guidance on demand** with the connector's `playbook` operation:

```python
loop   = call("odoo", {"operation": "playbook", "name": "00-diagnostic-loop"})["content"]
router = call("odoo", {"operation": "playbook", "name": "01-symptom-router"})["content"]
pb     = call("odoo", {"operation": "playbook", "name": "records-missing"})["content"]
```

`call("odoo", {"operation": "guidance"})["documents"]` lists every fetchable document.

**Symptom → playbook:**

| The user reports… | Fetch `name` |
|---|---|
| can't find records / empty list / "it's gone" / reference-number gaps | `records-missing` |
| access denied / can't edit / blank or missing fields | `access-denied` |
| P&L or balance-sheet totals wrong / wrong currency | `report-numbers-wrong` |
| won't save / validation error / duplicate reference / can't post-cancel | `write-failed` |
| slow / timeout / truncated results / stale data | `performance-timeout` |
| entry-name format / renumbering / move entries between journals | `sequence-journal` |
| duplicates / broken links / reconciliation off | `data-integrity` |
| start here to route any symptom | `01-symptom-router` |

Worked references (also fetchable by `name`): `user-activity-timeline`,
`resequence-and-rejournal`, `resequencing-and-journal-transitions`.

**Quick symptom hints (fast reference; the playbooks go deeper):**

- **External API unavailable**: Odoo docs note external API access depends on plan/hosting; verify permissions/plan if endpoints fail despite valid credentials.
- **AccessDenied**: wrong API key, username, database, or permissions. API keys replace passwords but do not log into the web UI.
- **HTTP 404**: wrong endpoint — use `/jsonrpc`, not `/jsonrpc/2/common`.
- **Private method not callable**: methods beginning with `_` are not available through `execute_kw`.
- **`browse()` serialization errors**: use `read()` or `search_read()`.
- **`False` field errors**: use safe helpers before slicing/indexing.
- **Oversized reads**: avoid reading all fields; pass explicit `fields` and paginate with `limit`/`offset`.
- **Report engine option errors**: call `get_options` with desired lightweight options, then pass returned options unchanged to `get_report_information`.
- **Syntax bracket errors**: keep `execute_kw` args flat; domain and kwargs are separate elements.
- **User not found by login**: fall back to `name ilike`.
