# Playbook — Records Missing / Empty List / "It's Not There"

Use when a user can't find records they expect: an empty list, a search that returns nothing,
a record that "disappeared," or "did we lose data?" Follow the loop from `00-diagnostic-loop.md`.
Everything here is read-only until the final, gated step — and most cases never need a write.

All calls use the platform shape `call("odoo", {...})`; the connector supplies credentials
server-side.

## Symptom signature

- "I can't see my invoices / products / customers / orders."
- "The list is empty but there should be records."
- "This record was here yesterday and now it's gone."
- "There are gaps in the reference numbers — did we lose products?"

## Observe (read-only)

Run these in order and record each actual result. Stop as soon as the results discriminate the
cause — you rarely need all of them.

```python
# O1. Does the data exist at all, ignoring the user's filter?
total = call("odoo", {"model": MODEL, "method": "search_count", "args": [[]]})

# O2. How many match the user's intended filter?
n = call("odoo", {"model": MODEL, "method": "search_count", "args": [USER_DOMAIN]})

# O3. Are matching-but-hidden records archived (active=False)?
n_incl_archived = call("odoo", {
    "model": MODEL, "method": "search_count",
    "args": [USER_DOMAIN + [["active", "in", [True, False]]]],
})

# O4. Sample a few rows to see what's actually there (small, explicit fields).
rows = call("odoo", {
    "model": MODEL, "method": "search_read",
    "args": [USER_DOMAIN],
    "kwargs": {"fields": ["id", "display_name", "create_date"], "limit": 5},
})

# O5. Confirm the field names in the filter are real (don't debug a typo'd field for an hour).
flds = call("odoo", {
    "model": MODEL, "method": "fields_get",
    "args": [], "kwargs": {"attributes": ["string", "type"]},
})
```

Interpretation rules:
- `total > 0` but `n == 0` → the **filter** is excluding everything (rule, domain, or company).
- `n == 0` but `n_incl_archived > 0` → the records are **archived**.
- `total == 0` → the data genuinely isn't in this database/company — move to Orient row "genuinely absent" and the sequence-gap check.
- O5 lists no such field → the domain uses a **wrong field name**; fix the query, not the data.

## Orient (ranked hypotheses)

Rank these top-down; this estate's priors are baked into the order.

| # | Hypothesis | The tell (from Observe) | Confirm with (read-only) |
|---|---|---|---|
| 1 | **Multi-company / record rule** hides rows from this user | `total` (or admin count) > user's `n` | `read` `res.users` `company_id`/`company_ids`; re-run O2 with the right company in `kwargs={"context": {"allowed_company_ids": [...]}}` |
| 2 | **Over-narrow or mis-typed domain** | Removing one clause makes rows appear; or O5 shows the field is wrong | Re-run O2 with the relaxed/corrected domain |
| 3 | **Archived records** (`active=False`) | O3 > O2 | Compare O2 vs O3; sample O4 with the `active` clause |
| 4 | **Wrong model or state** (e.g. draft vs posted, quotation vs order) | O4 rows exist but in another `state` | `search_read` grouping by `state` |
| 5 | **Base-36 sequence gap misread as loss** | User points at *number gaps*, not missing rows; `total` matches real records | See "Base-36 gaps" below |
| 6 | **Genuinely absent** (deleted, never created, wrong DB) | `total == 0` and all above ruled out | `search_count [[]]`; check `create_date` range of nearby records |

Prefer hypotheses 1–4 (data/config — yours to resolve) before concluding 6. In this environment,
"empty" is much more often a rule/company/domain effect than deletion.

## Base-36 gaps (special case — usually not a bug)

If the complaint is *gaps in reference codes* (product `default_code`, contact, or employee
references) rather than missing rows: those sequences are **non-transactional**. Any failed save,
aborted import, or rolled-back operation still consumes a code. **Gaps are expected and are not
data loss.** Confirm the underlying records are intact:

```python
# The records themselves are all present even though codes skip.
count = call("odoo", {"model": "product.product", "method": "search_count", "args": [[]]})
```

Then **explain rather than change anything**. Do not attempt to "renumber" to close gaps — that
risks the case-insensitive uniqueness constraint (`product_reference_unique_x`) and rewrites
audit-relevant references for a non-problem.

## Decide

- Hypotheses 1–4 confirmed → the fix is a **corrected query or a config/user-rights change**.
  A corrected read you run yourself (right company, right domain, include archived). If the real
  fix is unarchiving a record or changing a user's company access, that is a **write** — go
  through the write gate: state the record(s) and change, get confirmation.
- Base-36 gap → **explain, change nothing.**
- Genuinely absent → report it plainly with the evidence (`total == 0`, date range checked). If
  the user believes it was deleted, that is an audit question — offer to trace via `mail.message`
  / `ir.attachment` create/write metadata; do not assume.

Never "recreate" supposedly missing records to paper over an unconfirmed deletion.

## Act (only if a gated change was confirmed)

Example — unarchive specific, confirmed records (after explicit confirmation):

```python
# CONFIRMED WITH USER: unarchive partners [123, 124] ("ACME", "ACME Retail").
call("odoo", {"model": "res.partner", "method": "write", "args": [[123, 124], {"active": True}]})
```

## Confirm

Re-run the user's original filter (O2) and show that the expected rows now appear:

```python
rows = call("odoo", {
    "model": MODEL, "method": "search_read",
    "args": [USER_DOMAIN],
    "kwargs": {"fields": ["id", "display_name"], "limit": 10},
})
```

State "you searched X, saw nothing because Y; now the same search returns N rows." The loop is
only closed when this read confirms it.

## Escalate / hand off

- Records missing because of **server-side data migration, a restore, or hosting/plan limits** →
  outside the ORM; hand to the developer/odoo.sh flow with your evidence.
- Suspected deletion by a **code path** (e.g. a customization's `unlink`) → this is a code
  investigation, not a data fix; hand off with the model, IDs, and timeframe you established.
