# Playbook — Sequence & Journal Structure (Names, Resequencing, Journal Moves)

Use when the problem is the *structure* of accounting entry names or journals: reference/sequence
**format** changes (e.g. `/` → `-`), renumbering entries, or moving posted entries between journals
(e.g. opening balances into the true Opening Balances journal). Follow the loop from
`00-diagnostic-loop.md`. **This is the highest-risk playbook** — every fix touches posted,
audit-relevant records. Read-only until an accountant explicitly authorizes each step.

All calls use the platform shape `call("odoo", {...})`; the connector supplies credentials
server-side.

## Symptom signature

- "The journal entry names use the wrong format," "change the slashes to dashes."
- "These entries are in the wrong journal," "move the opening balances to the Opening Balances journal."
- "The numbering is out of order / has the wrong prefix."

## Critical background (read before acting)

- **Modern Odoo (v14+) does not use `ir.sequence` for `account.move` names.** Entry names are
  computed from the **latest posted entry** in the journal. So you cannot fix a name by editing an
  `ir.sequence` — you must use the proper wizard/flow.
- **Never use raw SQL or a naive `write` on `name`** to renumber. That desynchronizes partner
  ledgers, tax records, and internal pointers. Use `account.resequence.wizard`.
- **Posted entries are immutable** until reverted to draft. Rejournaling requires `button_draft` →
  write `journal_id` (and reset `name = "/"`) → `action_post`.
- **This estate's rule: any shell/verification is read-only and ends without commit; on shared
  builds you are working on production data.** As the connector model you cannot commit or roll back
  a DB transaction yourself — which is exactly why each write must be individually confirmed.

Deep worked recipes live in the connector's reference files (fetch them when doing this for real):
`references/resequence-and-rejournal.md` and `references/resequencing-and-journal-transitions.md`.
The audit-timeline recipe is `references/user-activity-timeline.md`.

## Observe (read-only)

```python
# O1. Read the affected moves exactly as they are: name, journal, state, date.
moves = call("odoo", {
    "model": "account.move", "method": "search_read",
    "args": [DOMAIN],
    "kwargs": {"fields": ["id", "name", "journal_id", "state", "date"],
               "order": "date asc, name asc", "limit": 200},
})

# O2. Identify the target journal (for a move) and confirm it exists.
journals = call("odoo", {
    "model": "account.journal", "method": "search_read",
    "args": [[["name", "ilike", TARGET_JOURNAL]]],
    "kwargs": {"fields": ["id", "name", "code", "type"]},
})

# O3. Preview a resequence WITHOUT applying it: create the wizard, read new_values, do NOT call resequence.
ctx = {"active_ids": MOVE_IDS, "active_model": "account.move"}
wizard_id = call("odoo", {
    "model": "account.resequence.wizard", "method": "create",
    "args": [{"first_name": DESIRED_FIRST_NAME, "ordering": "keep",
              "move_ids": [(6, 0, MOVE_IDS)]}],
    "kwargs": {"context": ctx},
})
preview = call("odoo", {
    "model": "account.resequence.wizard", "method": "read",
    "args": [[wizard_id], ["new_values"]],
})[0]
# Show preview["new_values"] to the user BEFORE any resequence call.
```

The wizard's `new_values` is a genuine dry-run — always show it and get sign-off before applying.

## Orient (ranked hypotheses)

| # | The user actually needs… | Correct mechanism | Never do |
|---|---|---|---|
| 1 | **Name format change** (`BNK1/2026/00001` → `BNK1-2026-00001`) | `account.resequence.wizard` (`ordering="keep"`), preview `new_values`, then `resequence` | SQL / `write` on `name` |
| 2 | **Reorder/renumber** a set of entries | Same wizard with `ordering` set appropriately | Manual per-record `write` |
| 3 | **Move posted entries to another journal** | `button_draft` → `write journal_id` + `name="/"` → `action_post` | Editing `journal_id` on a posted move |
| 4 | **Both** (rejournal + reformat) | Rejournal first (H3), then resequence (H1) on the new journal | Doing them in one blind step |

## Decide (gate every step)

For **each** state-changing call, STOP and get explicit confirmation from someone with accounting
authority, showing exactly which moves (IDs + names), the before/after, and reversibility:

- **Resequence (H1/H2):** confirm on the previewed `new_values`. It rewrites audit-relevant names —
  one confirmation per batch, not blanket.
- **Rejournal (H3):** three writes per move (`button_draft`, `write`, `action_post`), each with
  accounting consequences. Confirm the whole sequence and the specific moves before starting.
- If the entries are **reconciled, tax-reported, or lock-dated**, do not proceed on your own —
  hand off. These have downstream effects (VAT returns, locked periods) beyond the ORM call.

Prefer recommending the change with the exact wizard payload over executing it, unless the
authorized user clearly asks you to run it.

## Act (only after per-step confirmation)

Resequence — apply after the preview is approved:

```python
# CONFIRMED on previewed new_values. ordering="keep" preserves order, only reformats names.
call("odoo", {
    "model": "account.resequence.wizard", "method": "resequence",
    "args": [[wizard_id]], "kwargs": {"context": ctx},
})
```

Rejournal a posted move (each step separately, each confirmed):

```python
call("odoo", {"model": "account.move", "method": "button_draft", "args": [[MOVE_ID]]})
call("odoo", {"model": "account.move", "method": "write",
              "args": [[MOVE_ID], {"journal_id": TARGET_JOURNAL_ID, "name": "/"}]})  # name="/" resets seq calc
call("odoo", {"model": "account.move", "method": "action_post", "args": [[MOVE_ID]]})
```

Do a small batch first, confirm it, then continue — never fire the whole set blind.

## Confirm

Re-read the affected moves (O1) and show the new names/journals match the approved preview, and that
`state` is back to `posted`. State exactly what changed and that the ledgers stayed audit-sound
(names came from the wizard, not manual edits).

## Escalate / hand off

- **Reconciled / tax-reported / lock-dated entries**, or anything spanning a closed period → an
  accountant's decision; hand off with your read-only findings and the preview.
- **odoo.sh shell / migration-scale renumbering** → outside the connector; the developer/odoo.sh
  flow (with the read-only-then-rollback discipline) owns bulk operations on production data.
- If you're unsure whether a step is reversible, treat it as **not**, and escalate rather than guess.
