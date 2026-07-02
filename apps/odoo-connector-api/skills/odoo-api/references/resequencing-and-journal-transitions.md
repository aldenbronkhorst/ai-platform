# Resequencing and Journal Transitions in Odoo 18

Use these workflows for modifying sequence format or journal assignment of posted account moves through JSON-RPC.

## Dynamic Move Resequencing

Never edit account move names directly through SQL or plain writes. Use `account.resequence.wizard`.

Recipe:

1. Gather move IDs in the correct date/order.
2. Pass `active_ids` and `active_model` in context.
3. Create `account.resequence.wizard` with `first_name`, `ordering`, and `move_ids`.
4. Call `resequence`.

```python
ctx = {
    "active_ids": move_ids,
    "active_model": "account.move",
}

wizard_id = call("odoo", {
    "model": "account.resequence.wizard",
    "method": "create",
    "args": [{
        "first_name": "BNK01-2026-00001",
        "ordering": "keep",
        "move_ids": [(6, 0, move_ids)],
    }],
    "kwargs": {"context": ctx},
})

preview = call("odoo", {
    "model": "account.resequence.wizard",
    "method": "read",
    "args": [[wizard_id]],
    "kwargs": {"fields": ["new_values", "preview_moves"]},
})[0]

call("odoo", {
    "model": "account.resequence.wizard",
    "method": "resequence",
    "args": [[wizard_id]],
    "kwargs": {"context": ctx},
})
```

## Rejournaling Posted Account Moves

To move posted account moves to another journal:

1. Reset to draft with `button_draft`.
2. Write the target `journal_id` and set `name` to `/`.
3. Re-post with `action_post`.

```python
for move_id in move_ids:
    call("odoo", {
        "model": "account.move",
        "method": "button_draft",
        "args": [[move_id]],
    })

    call("odoo", {
        "model": "account.move",
        "method": "write",
        "args": [[move_id], {"journal_id": target_journal_id, "name": "/"}],
    })

    call("odoo", {
        "model": "account.move",
        "method": "action_post",
        "args": [[move_id]],
    })
```
