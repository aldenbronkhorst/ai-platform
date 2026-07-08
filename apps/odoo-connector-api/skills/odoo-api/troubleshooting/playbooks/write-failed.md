# Playbook — Write Failed / Can't Save / Validation Error

Use when a create or update is rejected: a validation error, a duplicate-reference complaint, a
required field, a uniqueness constraint, or "it won't let me confirm / post / cancel." Follow the
loop from `00-diagnostic-loop.md`. Diagnose read-only first; a blocked write is usually the system
protecting integrity — understand *why* before you retry.

All calls use the platform shape `call("odoo", {...})`; the connector supplies credentials
server-side.

## Symptom signature

- "It won't save," "I get a validation error," "required field missing."
- "Duplicate reference / that code already exists."
- "I typed a product reference and it saved as something else." (a **code trap** — see below)
- "I can't confirm / can't post / can't cancel this order."

## Observe (read-only)

```python
# O1. Capture the exact error text — the class and message decide everything.
#     ValidationError / UserError (business rule) vs. IntegrityError (DB constraint)
#     vs. AccessError (rights — go to access-denied.md).

# O2. Inspect the target field(s): type, required, selection values, help.
f = call("odoo", {
    "model": MODEL, "method": "fields_get",
    "args": [FIELDS], "kwargs": {"attributes": ["string", "type", "required", "selection", "help"]},
})

# O3. For a "duplicate" error, look for the existing record (case-insensitive!).
dupes = call("odoo", {
    "model": MODEL, "method": "search_read",
    "args": [[[REF_FIELD, "=ilike", REF_VALUE]]],
    "kwargs": {"fields": ["id", "display_name", REF_FIELD]},
})

# O4. For a state block, read the record's current state and what transitions are allowed.
rec = call("odoo", {"model": MODEL, "method": "read", "args": [[RECORD_ID], ["state", "display_name"]]})
```

Interpretation:
- O3 returns a row with a case-different code → a **case-insensitive uniqueness constraint** is
  firing (see estate notes). The "duplicate" is real even though it doesn't look identical.
- O4 shows a `state` the requested operation isn't valid from → a **state/business-rule** block,
  not a data error.
- IntegrityError with no visible dup → a DB-level constraint or a required relation; O2 tells you
  which field.

## Orient (ranked hypotheses)

| # | Hypothesis | The tell | Confirm with (read-only) |
|---|---|---|---|
| 1 | **Case-insensitive reference uniqueness** | "duplicate" on product/partner/employee code that looks different only in case | O3 with `=ilike` |
| 2 | **Required field / bad selection value** | `ValidationError`/`IntegrityError` naming a field | O2 `required`/`selection` |
| 3 | **State/business-rule guard** | "can't confirm/cancel/post"; friendly `UserError` | O4 `state`; see estate notes |
| 4 | **Reference auto-overwrite on create** (code trap) | Typed `default_code` came back as a base-36 code | Read template + variant `default_code` — see below |
| 5 | **Relational write shape wrong** | Error on a One2many/Many2many write | O2 field `type`; use Odoo command tuples |
| 6 | **Posted-entry immutability** | Can't edit a posted `account.move`/line | O4 `state == "posted"`; belongs to `sequence-journal.md` |

## Estate-specific notes (this deployment)

- **Case-insensitive reference uniqueness is enforced in code**, not just by a DB constraint:
  `product_reference_unique_x`, `partner_reference_unique_x`, and `hr_employee_reference_x` use
  `@api.constrains` with `sudo().search_count`. The product/partner modules also **dropped the
  legacy SQL unique constraint** (`product_product_default_code_uniq` / `res_partner_ref_uniq`), so
  a duplicate is caught by the Python constraint with a friendly message. Always check with
  `=ilike` — `ABC123` and `abc123` collide here.
- **Product Internal Reference overwrite on create (KNOWN CODE TRAP).** If a user typed a
  `default_code` on a `product.template` and it came back as an auto-generated base-36 code:
  `auto_generate_product_reference`'s `product.product.create()` override runs before the template's
  code propagates, sees a blank variant code, and generates one — which the stored compute then
  pulls back onto the template, discarding the typed value. **On production `lotslotsmore` this fix
  is NOT deployed** (it exists on `Staging`). You can *observe and confirm* the overwrite by reading
  both template and variant `default_code`, but the correction is **module code — hand off**
  (see `00-diagnostic-loop.md`). Do not try to "write the code back" repeatedly; on production the
  override will keep regenerating, and forcing it risks the uniqueness constraint in note 1.
- **State guards are intentional business rules.** `sale_order_cancel_restriction` blocks cancels
  in certain states; `vita_lesus_reset_to_draft` governs reset-to-draft. These raise `UserError`
  with a human message — the write "failing" is the module doing its job. Explain the rule and the
  legitimate state path; do not bypass it.
- **Do not defeat posted-entry protection with `sudo()`-style force paths.** `sh_stock_cancel`
  force-deletes posted journal entries / reconciliations / valuation layers via `sudo()`. That a
  force path exists is not permission to use it to make a write "go through" — it breaks accounting
  integrity and separation of duties.

## Decide

- **Duplicate reference (H1)** → the value genuinely collides. Decide with the user: use a
  different code, or (if the "duplicate" is itself unwanted data) treat it as a data-integrity
  question (`data-integrity.md`). Do not disable the constraint.
- **Required/selection/relational (H2, H5)** → the fix is a **corrected payload**: supply the
  required field, a valid selection value, or the right Odoo command tuple for relations. That's a
  legitimate gated write once the user confirms the intended values.
- **State/business-rule guard (H3)** → **explain the rule**; the path forward is moving the record
  through its proper transition, which itself may be a gated write the user must authorize.
- **Reference overwrite (H4)** → **hand off** as a code defect; confirm the overwrite by reading,
  but don't fight the override on production.
- **Posted entry (H6)** → go to `sequence-journal.md`; this is not a plain write.

## Act (only through the write gate)

Example — a corrected create after the user confirms the real values (not a code the module will
overwrite):

```python
# CONFIRMED WITH USER: create partner "ACME Foods", company, no manual ref.
new_id = call("odoo", {
    "model": "res.partner", "method": "create",
    "args": [{"name": "ACME Foods", "is_company": True}],
})
```

Relational writes use Odoo command tuples — inspect the field type first, then:

```python
# (6,0,ids)=replace, (4,id)=add, (1,id,{vals})=update, (0,0,{vals})=create-and-link
call("odoo", {"model": MODEL, "method": "write", "args": [[REC_ID], {"tag_ids": [(4, TAG_ID)]}]})
```

Never retry the identical failing write hoping it passes, and never route around a guard.

## Confirm

Re-read the record and show the write took (or, for an explanation outcome, show the constraint /
state / field evidence that proves the block is intended). "The save failed because <reason>; with
<corrected value / proper transition> it now saves as <result>."

## Escalate / hand off

- **Reference-overwrite trap** and any **module-code validation defect** → developer/odoo.sh path
  with the model, the typed vs. stored values, and the note that the fix may already be on `Staging`.
- **Posted-entry corrections** → `sequence-journal.md` under accounting authority.
- **A user asking to bypass a business guard** (`sale_order_cancel_restriction`, posted-entry
  protection) → decline the bypass, explain the rule, and route to the person who owns that policy.
