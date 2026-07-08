# Playbook — Access Denied / Permission Errors / Blank Fields

Use when a user hits an access error, can't open or edit a record they expect to, or sees fields
that are blank/`False` for them but populated for someone else. Follow the loop from
`00-diagnostic-loop.md`. All diagnosis here is read-only.

All calls use the platform shape `call("odoo", {...})`; the connector supplies the linked user's
credentials server-side — you act *as that user* and cannot escalate your own rights.

## Symptom signature

- "Access Denied," "You don't have access to this record," "operation not permitted."
- "I can open it but I can't edit / delete / confirm it."
- "This field is blank for me," "the token/secret doesn't show," "some fields are empty."
- API-side: an `AccessError` or `AccessDenied` returned from a call.

First, separate two very different failures:

- **`AccessDenied`** = authentication failed (bad database, login, or API key). The caller isn't
  who Odoo thinks they should be.
- **`AccessError`** = authenticated fine, but this user lacks the **model right** or is blocked by
  a **record rule** for this specific record/operation.

## Observe (read-only)

```python
# O1. Who am I acting as, and what groups do I have?
me = call("odoo", {
    "model": "res.users", "method": "read",
    "args": [[USER_ID], ["name", "login", "company_id", "company_ids", "groups_id"]],
})

# O2. Does the field even exist, and is it access-restricted?
f = call("odoo", {
    "model": MODEL, "method": "fields_get",
    "args": [[FIELD]], "kwargs": {"attributes": ["string", "type", "groups", "readonly"]},
})

# O3. Can I read the record at all (vs. read but not write)?
rec = call("odoo", {"model": MODEL, "method": "read", "args": [[RECORD_ID], ["id", "display_name"]]})

# O4. Model-level rights for my groups (which operations are allowed on this model).
#     ir.model.access is itself readable; check perm_read/write/create/unlink.
acl = call("odoo", {
    "model": "ir.model.access", "method": "search_read",
    "args": [[["model_id.model", "=", MODEL]]],
    "kwargs": {"fields": ["name", "group_id", "perm_read", "perm_write", "perm_create", "perm_unlink"]},
})
```

Interpretation:
- O2 shows the field has a non-empty `groups` and you aren't in one → the blank value is
  **field-gating**, not missing data. It reads as `False` by design.
- O3 succeeds but a write failed → **record rule or write-right**, not authentication.
- O3 itself raises `AccessError` → you lack even read on this record (rule or model right).
- Any call raises `AccessDenied` → authentication, not authorization — go to that branch below.

## Orient (ranked hypotheses)

| # | Hypothesis | The tell | Confirm with (read-only) |
|---|---|---|---|
| 1 | **Field-gated by group** (value hidden, returns `False`) | O2 `groups` set; user not in it | Compare O2 `groups` to O1 `groups_id` |
| 2 | **Record rule** restricts this row (often company/owner-scoped) | Reads of *other* rows work; this one doesn't; or read works, write doesn't | Read a sibling record; check `company_id` on the record vs O1 `company_ids` |
| 3 | **Model right missing** for the operation | O4 shows no `perm_write`/`perm_unlink` for the user's groups | O4 `ir.model.access` rows |
| 4 | **Wrong company context** | Record belongs to a company not in the user's allowed set | Re-read with `kwargs={"context": {"allowed_company_ids": [...]}}` |
| 5 | **Authentication** (`AccessDenied`) | Every call fails, not just one record/op | Verify db/login/API-key path (below) |
| 6 | **Intentional business restriction in a customization** | Error is a friendly `ValidationError`/`UserError`, not a raw `AccessError` | Read the record's `state`; see estate notes below |

## Estate-specific notes (this deployment)

- **Field-gated credentials are normal.** FNB bank tokens (`bank_first_national_bank_za_x` /
  `account_bank_api_link_x`), Bob Go (`delivery_bobgo`), and payment-provider credentials
  (`payment_bobpay`, `payment_payfast`) are **gated to `group_system`**. A non-admin user (or you,
  acting as one) reading those fields gets `False` — that is correct, not a fault. Never try to
  surface these secrets, and never instruct Workspace code to print them.
- **Some "access" errors are deliberate business rules, not ACL failures.** For example
  `sale_order_cancel_restriction` blocks cancelling orders in certain states, and
  `vita_lesus_reset_to_draft` governs reset-to-draft. These raise a `UserError`/`ValidationError`
  with a human message — treat them as *intended behaviour to explain*, not a permission bug to
  work around.
- **`sh_stock_cancel` uses broad `sudo()`** to force-delete posted accounting/stock records. If a
  user is asking to bypass an accounting restriction "because the cancel button lets me," that is
  a separation-of-duties risk, not a green light — do not help route around posted-entry
  protections through the ORM.
- **Public-group reads exist by design** (e.g. `delivery.x.type`/`delivery.x.courier` are readable
  by `base.group_public`). A website visitor reading courier config is expected.

## Decide

- **Field-gated (H1)** → explain that the field is restricted to an admin/system group and reads as
  empty for this user by design. Do not attempt to escalate. If the user legitimately needs it,
  the path is an Odoo admin granting the group — a **config change a human makes**, not an ORM
  write you perform blindly.
- **Record rule / model right / company (H2–H4)** → explain precisely what's blocked and why
  (which rule/company). If the resolution is a rights or company-access change, that is a **write
  to `res.users`/groups or rule config** — high-impact; go through the write gate and prefer
  handing the exact change to an Odoo admin over doing it silently.
- **Authentication (H5)** → see below; this is a credential/config fix, usually a human step.
- **Business-rule restriction (H6)** → explain the rule and the legitimate path (e.g. the order
  must leave its current state first). Do not use `sudo()`-style bypasses or posted-entry state
  changes to defeat an intended guard.

Granting rights, changing group membership, or altering record rules to make an error go away is
almost never the right *first* move. Diagnose and explain first; change access only with explicit
confirmation and a clear business reason.

## Authentication branch (`AccessDenied`)

If calls fail with `AccessDenied` regardless of record:
- The connector authenticates server-side with the linked user's db/login/API key. An API **key**
  replaces the password in API calls but does **not** log into the web UI — a key that "works in
  the browser" is the wrong credential type.
- Likely causes: wrong database name, wrong login, revoked/rotated API key, or the external API
  being disabled for the plan/hosting. These are **credential/config** fixes made in Connected
  Accounts or by an admin — not something you resolve by retrying calls.
- Report which of (db, login, key, plan) is the likely culprit from the error, and route the user
  to re-link the account. Never ask for or print the secret.

## Act (only if a gated change was confirmed)

Access changes are high-impact and usually belong to an Odoo admin. If — and only if — the user
with authority explicitly confirms a specific change, make the minimal one and state it exactly.
Prefer recommending the change over performing it. Never widen a group, disable a record rule, or
grant `perm_unlink` as a convenience.

## Confirm

Re-attempt the *originally failing* read/operation as the same user and show it now succeeds (or,
for an explanation-only outcome, show the field's `groups`/rule that proves the behaviour is
intended). Close the loop with "you couldn't do X because Y; here is the evidence / here is the
now-successful result."

## Escalate / hand off

- Authentication or plan/hosting problems, and anything requiring **admin config on the live DB**
  (2FA, API-key scoping, group design) → hand to the human/admin path with your diagnosis.
- If you suspect a customization is raising a *spurious* `AccessError` (a code bug, not a rule) →
  that's a code investigation outside the ORM; hand off with the model, record, operation, and the
  exact error.
