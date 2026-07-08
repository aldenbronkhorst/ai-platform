# Playbook — Data Integrity (Duplicates, Broken Links, Reconciliation Off)

Use when the data itself looks inconsistent: duplicate customers/products, broken or dangling
relations, reconciliation that doesn't balance, or mismatched partner references. Follow the loop
from `00-diagnostic-loop.md`. Integrity fixes are among the most destructive things you can do —
diagnose thoroughly read-only, and prefer *merge/repair through Odoo's own tools* over `unlink`.

All calls use the platform shape `call("odoo", {...})`; the connector supplies credentials
server-side.

## Symptom signature

- "There are duplicate customers / products / contacts."
- "The link is broken," "it points to nothing," "the related record is missing."
- "Reconciliation is off," "the partner ledger doesn't match," "payments aren't matching invoices."
- "Two records that should be one."

## Observe (read-only)

```python
# O1. Quantify the suspected duplicates by the key that should be unique (case-insensitive!).
sample = call("odoo", {
    "model": MODEL, "method": "search_read",
    "args": [[[KEY_FIELD, "!=", False]]],
    "kwargs": {"fields": ["id", "display_name", KEY_FIELD], "limit": 500, "order": KEY_FIELD},
})
# Group in your head / code by KEY_FIELD.lower() to find real collisions.

# O2. For a "broken link," read the relation and check whether the target resolves.
rec = call("odoo", {"model": MODEL, "method": "read", "args": [[REC_ID], [REL_FIELD]]})
# Many2one returns [id, display_name] or False. If [id, ...] but the id no longer reads, it's dangling.

# O3. For reconciliation, look at the reconciled state and the partner on both sides.
lines = call("odoo", {
    "model": "account.move.line", "method": "search_read",
    "args": [DOMAIN],
    "kwargs": {"fields": ["id", "move_id", "partner_id", "account_id", "balance",
                          "reconciled", "amount_residual"], "limit": 200},
})

# O4. Count first if you might be about to touch many records.
n = call("odoo", {"model": MODEL, "method": "search_count", "args": [DOMAIN]})
```

Interpretation:
- O1 rows whose `KEY_FIELD` differ only by case → real duplicates that the case-insensitive
  constraint would now block (they likely predate it, or came in via import).
- O2 returns a Many2one id that itself won't `read` → a **dangling reference**.
- O3 `amount_residual != 0` on lines the user thinks are settled → a **reconciliation gap**, often a
  partner mismatch.

## Orient (ranked hypotheses)

| # | Hypothesis | The tell | Confirm with (read-only) |
|---|---|---|---|
| 1 | **True duplicates** (same entity, two records) | O1 collisions on name/ref/email/VAT | Sample both records' key fields side by side |
| 2 | **Not duplicates, just similar** | Same name, different VAT/ref/company | Compare distinguishing fields before merging |
| 3 | **Dangling / broken relation** | O2 id doesn't resolve | Re-`read` the target id directly |
| 4 | **Reconciliation mismatch by partner ref** | O3 residuals; partner differs across matched lines | See estate note on `bank_reconciliation_by_partner_ref` |
| 5 | **Apparent gap that's actually by-design** | "missing" reference numbers | Base-36 non-transactional gaps — not an integrity fault (see below) |

## Estate-specific notes (this deployment)

- **Case-insensitive uniqueness is enforced going forward, but old duplicates can exist.**
  `product_reference_unique_x` / `partner_reference_unique_x` / `hr_employee_reference_x` block *new*
  collisions, and the product/partner ones dropped the legacy SQL constraint. Pre-existing
  duplicates (from before these modules, or from imports) won't have been retro-cleaned — so
  "duplicates exist" is plausible even though new ones are blocked. Confirm with `=ilike`.
- **Base-36 reference gaps are NOT an integrity problem.** Skipped `default_code` / contact /
  employee numbers come from non-transactional sequences (rolled-back or failed saves still consume a
  code). Do not "repair" them — renumbering would rewrite audit-relevant references and risk the
  uniqueness constraint. Reassure and move on (cross-ref `records-missing.md`).
- **Reconciliation by partner reference:** `bank_reconciliation_by_partner_ref` matches bank lines to
  invoices via the partner reference. A "reconciliation off" symptom is frequently a **partner-ref
  mismatch** (typo, wrong ref on the invoice or the bank line), not a broken ledger. Check the refs
  on both sides before touching reconciliations.
- **Do NOT use `sh_stock_cancel`-style force paths to "clean up."** That module force-deletes posted
  journal entries, reconciliations, and stock valuation layers via `sudo()`, bypassing record rules.
  Using it to remove a "duplicate" or "bad" record destroys accounting integrity and separation of
  duties. Integrity repair never justifies a force-delete of posted accounting data.

## Decide

- **True duplicates (H1)** → the right tool is a **merge**, not a delete. For partners, Odoo's
  contact-merge preserves links (moves references to the surviving record); a raw `unlink` orphans
  everything that pointed at the removed record. Decide *which record survives* with the user, and
  prefer merge. Any merge/delete is a high-impact gated write — confirm explicitly, and never batch.
- **Not-actually-duplicates (H2)** → explain the distinguishing fields; change nothing.
- **Dangling relation (H3)** → identify what should be pointed to and, with confirmation, re-point
  the Many2one (a targeted `write`). Do not delete the holder to make the error disappear.
- **Reconciliation mismatch (H4)** → fix the **reference/matching**, not the ledger balances.
  Correcting a partner ref so matching works is a gated write; re-running reconciliation is the
  mechanism. Don't adjust amounts to force a balance.
- **By-design gap (H5)** → explain, change nothing.

Bias to the least destructive action: re-point over delete, merge over unlink, correct-the-key over
rewrite-the-data. When in doubt, present the evidence and let a human decide.

## Act (only through the write gate, smallest safe step first)

Re-point a dangling relation (after confirmation):

```python
# CONFIRMED WITH USER: move.line 5512 should reference partner 341 ("ACME Foods").
call("odoo", {"model": "account.move.line", "method": "write", "args": [[5512], {"partner_id": 341}]})
```

For partner de-duplication, prefer Odoo's merge wizard (`base.partner.merge.automatic.wizard`) over
`unlink`, and do one confirmed pair at a time so links are preserved and reversible in review.

Never `unlink` posted accounting records or force-delete through a `sudo()` cancel path to resolve
an integrity issue.

## Confirm

Re-read the affected records and show the collision is resolved / the relation now resolves / the
residual is cleared — with the specific IDs. State what was changed and, importantly, that nothing
was force-deleted and no posted data was rewritten.

## Escalate / hand off

- **Reconciliation touching tax-reported or lock-dated periods** → accountant authority.
- **Bulk de-duplication or an import that created the duplicates** → this is a data-migration job,
  better done deliberately (and reversibly) via the developer/odoo.sh flow than record-by-record
  through the connector; hand off with your duplicate inventory.
- **Suspected corruption from a code path** (a customization writing bad relations) → code
  investigation outside the ORM; hand off with the model, the dangling IDs, and when they appeared.
