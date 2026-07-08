# Odoo Troubleshooting — Symptom Router

Start here when a user reports something wrong with Odoo. Match their words to a row, open the
linked playbook, and run its loop. If two rows fit, open both and let the Observe step decide.
You must already be following `00-diagnostic-loop.md` (read-only first, write gate, handoff
boundary).

## How to use this

The user rarely names a model or a method — they describe a *symptom*. Your job is to turn the
symptom into the right diagnostic path, not to guess a fix. Read the "user says" column for the
match, note the "first read" as your opening Observe call, and go to the playbook.

## Symptom → playbook

| User says something like… | Likely area | Open | First read (Observe) |
|---|---|---|---|
| "I can't find / can't see …", "the list is empty", "records disappeared", "it's not there anymore" | Records missing | `playbooks/records-missing.md` | `search_count` on the model with `[]`, then with the user's filter |
| "there are gaps in my product / reference numbers", "we skipped codes", "did we lose products?" | Sequence gaps (usually **not a bug**) | `playbooks/records-missing.md` → "Base-36 gaps" | `search_count` on the model; compare to the sequence |
| "access denied", "you don't have access", "I can't open / edit this", "permission error" | Access / rights | `playbooks/access-denied.md` | `read` the record's model via `fields_get`; check user groups |
| "the field is blank / says False", "the token / secret isn't showing", "some fields are empty for me" | Field gating / record rules | `playbooks/access-denied.md` → "Field-gated" | `fields_get` on the field; `read` as-is |
| "my P&L / balance sheet is wrong", "the totals don't match", "the report shows the wrong number / currency" | Report figures | `playbooks/report-numbers-wrong.md` | `account.report` `get_options` → `get_report_information` |
| "it won't save", "validation error", "duplicate reference", "required field", "can't confirm / can't post" | Write blocked | `playbooks/write-failed.md` | `fields_get` on the model; read the constraint target |
| "I typed a product reference and it saved as something else" | Customization trap (**code**) | `playbooks/write-failed.md` → "Reference overwrite" | `read` `default_code` on template + variant |
| "it's slow", "it timed out", "the query hangs", "results were truncated" | Performance | `playbooks/performance-timeout.md` | `search_count` before any wide `search_read` |
| "reference numbers use the wrong format", "journal names are wrong", "opening balances are in the wrong journal" | Sequence / journal structure | `playbooks/sequence-journal.md` | read `name`/`journal_id` on the affected `account.move`s |
| "duplicate customers / products", "the links are broken", "reconciliation is off" | Data integrity | `playbooks/data-integrity.md` | `search_read` grouped/sampled on the suspected duplicate key |

## Known-code-bug fast lane

Some reports match a **known defect in this estate's module code** — already diagnosed, and in
several cases already fixed on `Staging` but not on production. When the user's words match one
of these, you still Observe to confirm the signature, but your Decide is **handoff**, not an ORM
fix (see `00-diagnostic-loop.md` → "The handoff boundary"):

| User says… | Confirmed signature to look for | Decision |
|---|---|---|
| "bank reconciliation is crashing", "error when I reconcile a payment line" | `AttributeError` on `move_id.payment_id` during bank rec; only on payment-linked lines | **Hand off** — Odoo-18 field rename fixed on `Staging` (`origin_payment_id`), pending promotion. Do not edit moves to dodge it. |
| "the product reference I typed got replaced", "my Internal Reference changed to a code" | Template `default_code` typed by user, came back as a base-36 code on both template and variant | **Hand off** — `create()` override trap; fix on `Staging`. Confirm the overwrite by reading, but the correction is code. |
| "there are gaps in the reference numbers" | Sequence advanced past visible records; no missing *data* | **Explain, don't change** — base-36 sequences are non-transactional; gaps are expected. |
| "attendance in/out are swapped", "clock times are hours off", "someone shows breakIn on the way out" | Hikvision punch pairing/direction or device-clock artifacts | **Hand off** — device-integration bridge issue on the ops box + a Staging-only module; not fixable via Odoo ORM data edits. |

## When nothing matches

If the symptom fits no row, do not force a playbook. Run the generic loop from
`00-diagnostic-loop.md`: Observe with the narrowest read that could reproduce the user's
experience, Orient from what comes back, and Decide the next read. Escalate to a human when the
evidence points at server config, hosting/plan limits, or module code you cannot reach.
