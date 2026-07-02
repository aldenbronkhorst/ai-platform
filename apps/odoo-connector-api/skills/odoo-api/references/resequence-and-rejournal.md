# Resequencing and Rejournaling Posted Entries in Odoo 18

When working with financial sequences and correcting misaligned transactions, Odoo's strict posted-entry rules prevent simple database writes. Use official ORM workflows via JSON-RPC.

## Safe Rejournaling of Posted Entries

If transactions were recorded in the wrong journal, you cannot directly write `journal_id` on a posted move.

Correct ORM workflow:

1. Reset to draft with `button_draft`.
2. Write the new `journal_id` and set `name = "/"`.
3. Re-post with `action_post`.

```python
call("odoo", {
    "model": "account.move",
    "method": "button_draft",
    "args": [[move_id]],
})

call("odoo", {
    "model": "account.move",
    "method": "write",
    "args": [[move_id], {"journal_id": destination_journal_id, "name": "/"}],
})

call("odoo", {
    "model": "account.move",
    "method": "action_post",
    "args": [[move_id]],
})
```

## Dynamic Resequencing via `account.resequence.wizard`

In Odoo 14+, sequence numbers are calculated dynamically. To change formatting, use Odoo's native resequencing wizard.

Steps:

1. Fetch move IDs for the target journal in chronological order.
2. Pass `active_ids` and `active_model` in the RPC context.
3. Create the wizard with `first_name`, `ordering`, and `move_ids`.
4. Call the public `resequence` method.

```python
ctx = {
    "active_ids": move_ids,
    "active_model": "account.move",
}

wizard_id = call("odoo", {
    "model": "account.resequence.wizard",
    "method": "create",
    "args": [{
        "first_name": "OPN-2026-00001",
        "ordering": "keep",
        "move_ids": [(6, 0, move_ids)],
    }],
    "kwargs": {"context": ctx},
})

call("odoo", {
    "model": "account.resequence.wizard",
    "method": "resequence",
    "args": [[wizard_id]],
    "kwargs": {"context": ctx},
})
```

## Prefix and Formatting Guidelines

- Use 5-digit sequences for high-volume journals.
- Avoid month segments in primary sequences unless explicitly requested.
- Prefer a continuous annual format such as `OPN-2026-00001`.
