# Playbook — Slow / Timeout / Truncated Results

Use when a query is slow, times out, hangs, or comes back truncated/incomplete — or when data the
user expects is simply stale because a scheduled sync didn't run. Follow the loop from
`00-diagnostic-loop.md`. The fix is almost always **query shape** (narrower reads, pagination) or
**upstream freshness**, not a data change.

All calls use the platform shape `call("odoo", {...})`; the connector supplies credentials
server-side.

## Symptom signature

- "It's slow," "it timed out," "the query hangs / spins."
- "The results were cut off," "it says truncated / incomplete."
- "Yesterday's / today's data isn't showing" (often a **stale sync**, not a live query problem).

## Observe (read-only)

Never open with a wide `search_read`. Size the problem first.

```python
# O1. How big is the result set BEFORE you fetch it?
n = call("odoo", {"model": MODEL, "method": "search_count", "args": [DOMAIN]})

# O2. Sample a tiny, explicit slice — never all fields.
sample = call("odoo", {
    "model": MODEL, "method": "search_read",
    "args": [DOMAIN],
    "kwargs": {"fields": ["id", "display_name"], "limit": 5, "order": "id desc"},
})

# O3. If this is "data is stale," check freshness rather than volume.
latest = call("odoo", {
    "model": MODEL, "method": "search_read",
    "args": [[]],
    "kwargs": {"fields": ["id", "create_date", "write_date"], "limit": 1, "order": "write_date desc"},
})

# O4. If a scheduled job is suspected, read its state (cron records are readable).
crons = call("odoo", {
    "model": "ir.cron", "method": "search_read",
    "args": [[["name", "ilike", JOB_NAME]]],
    "kwargs": {"fields": ["name", "active", "nextcall", "lastcall"], "limit": 10},
})

# O5. Recent logged errors (helps spot silent failures).
errs = call("odoo", {
    "model": "ir.logging", "method": "search_read",
    "args": [[["level", "in", ["ERROR", "WARNING"]], ["create_date", ">=", SINCE]]],
    "kwargs": {"fields": ["name", "message", "create_date"], "limit": 50, "order": "create_date desc"},
})
```

Interpretation:
- O1 large (tens of thousands+) and you asked for many fields → the query is oversized; **paginate
  and trim fields**.
- O3 `write_date` older than the user expects → the data is **stale**, not slow; the problem is an
  upstream sync (O4/O5), not your query.
- O4 `nextcall` in the past or `lastcall` stale while `active` → the cron isn't running / is failing.

## Orient (ranked hypotheses)

| # | Hypothesis | The tell | Confirm with (read-only) |
|---|---|---|---|
| 1 | **Oversized read** (too many rows and/or all fields) | O1 large; request had no/large `limit` or no `fields` | Re-run with small `limit` + explicit `fields` |
| 2 | **Truncated output handled wrongly** | Tool result flagged truncated/incomplete | Re-query narrower — never infer the missing rows |
| 3 | **Unindexed / heavy domain** (e.g. `ilike` on a huge text field, cross-model traversal) | Small result but slow | Narrow the domain; filter on indexed fields first |
| 4 | **Stale upstream sync** (data-freshness, not query speed) | O3 stale; O4 cron overdue; O5 shows failures | O4 `ir.cron` nextcall/lastcall; O5 `ir.logging` |
| 5 | **Server/plan/hosting limit** | Everything is slow regardless of query | Outside the ORM — escalate |

## Estate-specific notes (this deployment)

- **The FNB bank-sync cron can fail silently.** If the complaint is "recent bank transactions
  aren't showing," this is very likely **stale data**, not a slow report: the scheduled sync
  (`bank_first_national_bank_za_x` / `account_bank_api_link_x`) failed without notifying anyone.
  Check `ir.cron` (`nextcall`/`lastcall`) and `ir.logging` for the failure. The durable fix is
  enabling failure alerts on the cron — an **admin/ops action**, not a data edit.
- **Truncation discipline is a hard rule** (echoing the base prompt): if a result says it was
  truncated or incomplete, **never infer the missing records from naming patterns or sequence** —
  run a narrower query (tighter domain, smaller `limit`, explicit `fields`) or state plainly that
  the output was incomplete. In this estate that matters doubly because base-36 reference codes look
  sequential but have real gaps — you cannot "fill in" what you didn't read.
- **Reading all fields is the usual culprit.** Many models here (accounting moves, stock moves,
  products with variants) carry heavy fields. Always pass explicit `fields` and paginate with
  `limit`/`offset`.

## Decide

- **Oversized/heavy query (H1–H3)** → the fix is a **better read you run**: explicit `fields`, a
  small `limit`, `offset` pagination, and an indexed/narrowed domain. Use `search_count` +
  `read_group` for totals instead of pulling every row. No data change.
- **Stale sync (H4)** → this is **not a query fix and not yours to write**. Report that the data is
  stale and the sync is overdue/failing, with the `ir.cron`/`ir.logging` evidence, and route to the
  admin to re-run it and add failure alerts.
- **Server/plan limit (H5)** → escalate; outside the ORM.

Do not "speed up" anything by creating, deleting, or rewriting records.

## Act

Paginate instead of one giant read:

```python
# Pull in pages; stop when a page returns fewer than page_size.
page_size, offset, out = 200, 0, []
while True:
    rows = call("odoo", {
        "model": MODEL, "method": "search_read",
        "args": [DOMAIN],
        "kwargs": {"fields": ["id", "name", "amount_total"], "limit": page_size,
                   "offset": offset, "order": "id asc"},
    })
    out += rows
    if len(rows) < page_size:
        break
    offset += page_size
```

Prefer aggregation when you only need totals:

```python
groups = call("odoo", {
    "model": "account.move.line", "method": "read_group",
    "args": [[["parent_state", "=", "posted"]], ["balance"], ["account_id"]],
})
```

## Confirm

Show the same information returned quickly and completely via the narrower/paginated approach, or —
for a staleness outcome — show the cron/log evidence and the freshness gap. Close with "the read was
slow/truncated because <oversize/domain>; scoped as <fix> it returns <N> rows cleanly," or "the data
was stale because <cron> last ran <when>."

## Escalate / hand off

- **Silent cron failures / stale syncs** → admin/ops to re-run and to add failure notifications;
  this is a monitoring gap, not a data bug.
- **Persistent slowness independent of query shape** → server/plan/hosting; hand to the odoo.sh/ops
  path with your timing evidence.
