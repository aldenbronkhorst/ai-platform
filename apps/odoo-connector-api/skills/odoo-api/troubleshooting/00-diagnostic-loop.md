# Odoo Troubleshooting — The Diagnostic Loop

This is the spine every Odoo troubleshooting task follows. Read it once, then enter the
specific playbook the symptom router points you to. The playbooks assume you are already
operating by the rules below.

## Your surface (what you can and cannot do)

You reach Odoo **only** through the connector broker target `odoo`. Every call uses the
platform shape — `call("odoo", {"model": ..., "method": ..., "args": [...], "kwargs": {...}})`
— and the connector injects the linked user's database, uid, and API key **server-side**. You
never see or pass those credentials; do not write `execute_kw`-with-credentials code or print
secrets (see the main `SKILL.md`). That means:

- You **can** read and (when confirmed) write Odoo data through the ORM: `search_read`,
  `search_count`, `read`, `fields_get`, `create`, `write`, `unlink`, model wizards.
- You **cannot** touch the server, the filesystem, git, the odoo.sh build, module source
  code, logs on disk, or the database directly. You have no shell.

This boundary decides many cases below: if the fix requires changing **module code** or a
**deployment**, that is not something you can do through `execute_kw` — your job there is to
*diagnose precisely and hand off*, not to improvise an ORM workaround around a code defect.

## The loop: Observe → Orient → Decide → Act

Run every investigation as this loop. Do not skip to Act.

**Observe — read-only, always.** Gather the actual system state with non-mutating calls
only (`search_count`, `search_read` with a small `limit` and explicit `fields`,
`fields_get`, `ir.model`, `ir.model.fields`). Treat "empty result" and "error message" as
real observations, not dead ends. Never mutate data while you are still diagnosing.

**Orient — rank hypotheses, don't pick the first.** Map what you observed to a *ranked* list
of causes using Odoo priors and this estate's known traps (the playbooks give you the
tables). Rank by likelihood, not by ease of fixing. In this environment an unexpected result
is far more often a record-rule / multi-company / field-gating effect, a customization trap,
or a known-and-already-fixed code bug than it is genuine data loss.

**Decide — the narrowest safe next step.** Usually that is *one more read* that discriminates
between your top two hypotheses. Only move to a write when the diagnosis is confirmed, and
only through the write gate below. If the confirmed cause is module code or deployment,
decide to **hand off**, not to patch around it.

**Act — then loop back to Observe.** Make the one chosen call. Then re-observe to confirm the
effect. A fix is not "done" until a read proves it. If the read doesn't confirm, you have a
new observation — loop again; do not repeat the same action hoping for a different result.

## The write gate (non-negotiable)

Diagnosis is read-only. Before **any** state-changing call — `create`, `write`, `unlink`, or
a wizard method that posts/cancels/deletes/resequences — you STOP and get explicit user
confirmation, stating in plain language: the model, the exact records (IDs and names), the
change, and whether it is reversible. Never batch a write into a diagnostic step. Never run
`unlink` or a posted-entry state change (`button_draft`, `action_post`, cancel/resequence
wizards) without a clear, confirmed reason and, where possible, a preview read first.

Bias hard toward read-only. Most troubleshooting ends with an *explanation and a
recommendation*, not a mutation.

## Keep a diagnostic log

As you work, keep a short running record the user can follow: what you observed (the actual
call and the actual result), which hypothesis it supported or ruled out, and what you decided
next. This is what makes the reasoning auditable and lets you — or a human — resume mid-loop.
Present findings as "observed X → therefore Y → next Z," not as a bare conclusion.

## The handoff boundary — data/config vs. code/deployment

This is the most important decision you make, and this estate makes it concrete. Some symptoms
are **known code defects** that live in custom module source, several of which are *already
fixed on the `Staging` branch but not yet promoted to production*. You cannot fix those through
`execute_kw`, and you must not try to ORM-hack around them (e.g. do not start rewriting posted
records by hand to dodge a Python `AttributeError` in module code).

When your Orient step lands on a code/deployment cause, switch to **handoff mode**: state the
precise defect, the evidence (the exact error or the field values that prove it), that it is a
code-level issue outside the ORM's reach, and — if known — that a fix exists on `Staging`
pending promotion. Recommend the developer/odoo.sh path. Do not attempt the fix yourself.

Known code-level cases you should recognize and hand off (not patch):

- **Bank reconciliation crashes with `AttributeError` on payment-linked lines.** Production
  reads a field removed in Odoo 18 (`move_id.payment_id`); the corrected `origin_payment_id`
  line exists on `Staging`. This is a promotion gap, not a data problem — do not edit moves to
  work around it.
- **A typed product Internal Reference is silently replaced on create** by an auto-generated
  base-36 code. This is a `create()` override trap in a customization; the fix is on `Staging`.
  You can *observe and confirm* the overwrite, but the correction is code, not data.
- **Gaps in product / contact / employee reference sequences.** Base-36 sequences are
  **non-transactional** — rolled-back or failed saves still consume a code. Gaps are **expected,
  not data loss.** The correct action here is usually to *reassure and explain*, not to change
  anything.

By contrast, symptoms rooted in **data or configuration** — a wrong domain, an archived record,
a record rule, a missing user right, a report option, a mis-set field — are yours to diagnose
and, with confirmation, fix through the ORM. The playbooks tell you which is which.

## Entering a playbook

Go to `01-symptom-router.md`, match the symptom, and open the referenced playbook. Each
playbook is structured the same way: **Symptom signature → Observe → Orient (ranked
hypotheses) → Decide → Act → Confirm → Escalate.** Follow it in order.
