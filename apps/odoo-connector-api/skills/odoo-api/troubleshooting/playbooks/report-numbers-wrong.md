# Playbook — Report Numbers Wrong / Totals Don't Match

Use when a financial report looks wrong: a P&L or Balance Sheet total the user disputes, a
figure that doesn't match what they expect, a wrong currency, or "the numbers changed." Follow
the loop from `00-diagnostic-loop.md`. This is almost always a **reading/options/period** problem,
not corrupt data — diagnose read-only and resist the urge to "correct" entries.

All calls use the platform shape `call("odoo", {...})`; the connector supplies credentials
server-side.

## Symptom signature

- "My P&L / income statement / balance sheet is wrong."
- "The total doesn't match what I calculated / what the bank says."
- "It's showing the wrong currency / the amounts look converted."
- "This number was different yesterday."
- "The report is missing this month's transactions."

## Observe (read-only)

Use Odoo's own report engine as the source of truth — do not hand-sum `account.move.line` as your
first move. Reproduce exactly what the user sees.

```python
# O1. Identify the report the user means. search_read returns a LIST — pick the intended one.
matches = call("odoo", {
    "model": "account.report", "method": "search_read",
    "args": [[["name", "ilike", REPORT_NAME]]],
    "kwargs": {"fields": ["id", "name"], "limit": 5},
})
# Choose the right match if several come back (P&L vs "P&L (comparison)", etc.).
report = matches[0]  # after confirming matches is non-empty and this is the intended report

# O2. Build options with the user's exact period FIRST (date drives column_groups).
previous_options = {
    "date": {"mode": "range", "date_from": DATE_FROM, "date_to": DATE_TO, "filter": "custom"},
    "unfolded_lines": [], "unfold_all": False,
}
options = call("odoo", {
    "model": "account.report", "method": "get_options",
    "args": [[report["id"]], previous_options],
})

# O3. Read the report exactly as rendered.
data = call("odoo", {
    "model": "account.report", "method": "get_report_information",
    "args": [[report["id"]], options],
})
# Values: line["columns"][i]["no_format"] = raw number; ["name"] = displayed text.

# O4. What options did Odoo actually apply? (posted-only? which companies? which currency?)
#     Inspect options for: date, "all_entries"/"unposted", "companies", "currency"/"multi_currency".
```

Interpretation:
- If O3 matches the user's screen, the "wrong" number is real *given these options* — the problem
  is almost always in the **options** (period, posted vs. draft, company, currency), so inspect O4.
- If O3 differs from the user's screen, they are looking at a **different period/filter/company**
  than they think — reconcile the options before anything else.

## Orient (ranked hypotheses)

| # | Hypothesis | The tell | Confirm with (read-only) |
|---|---|---|---|
| 1 | **Wrong period / date filter** | O4 `date` differs from user's intent; "missing this month" | Re-run O2/O3 with the intended `date_from/date_to` |
| 2 | **Draft vs posted entries included/excluded** | Total off by specific unposted entries | Compare O3 with options `all_entries` toggled; `search_count` on `account.move` by `state` in range |
| 3 | **Multi-company scope** | O4 `companies` broader/narrower than expected | Read `options` companies; compare to the user's company |
| 4 | **Currency / conversion** | Amounts look converted; report in company currency, user expects a doc currency | Check O4 currency; read the record's own `currency_id` vs `res.company.currency_id` |
| 5 | **Tax / rebate customization effects** | Tax or net figures differ from a naive sum | See estate notes — `account_manual_tax_preserve_x`, `account_rebate_x` |
| 6 | **Accounting-date vs document-date** | Vendor bills land in an unexpected period | See estate notes — `account_bill_accounting_date_default_x` |
| 7 | **Stale upstream data** (sync didn't run) | Bank lines the user expects simply aren't there | See estate notes — silent FNB cron |
| 8 | **Genuinely mis-posted entry** | All above ruled out; a specific move is in the wrong account/period | `search_read` the suspect `account.move.line` (read-only) |

Rank 1–4 first; period/posted/company/currency explains the large majority of "wrong total"
reports. Reach hypothesis 8 (real data problem) only after the options are proven correct.

## Estate-specific notes (this deployment)

- **`account_manual_tax_preserve_x`** preserves manually-entered tax amounts rather than
  recomputing them. A report total that disagrees with a naive `price * tax%` may be *correct* —
  the manual tax was intentionally preserved. Read the move line's tax fields before calling it
  wrong.
- **`account_rebate_x`** introduces rebate handling that affects net figures. Don't treat a rebate
  adjustment as a missing/duplicated amount.
- **`account_payment_currency_rate_x`** governs the rate applied to foreign-currency payments. A
  "wrong converted amount" often traces to the rate on the payment, not the report.
- **`account_bill_accounting_date_default_x`** defaults the *accounting date* of vendor bills,
  which decides the reporting period independently of the invoice date. "This bill is in the wrong
  month" is usually this, working as designed.
- **FNB bank sync cron can fail silently** (`bank_first_national_bank_za_x` /
  `account_bank_api_link_x`). If expected bank lines are simply absent, the sync may not have run —
  this is an **upstream data-freshness** issue, not a report bug. Check for recent bank statement
  lines before blaming the report (and see `performance-timeout.md` for the cron-failure check).

## Decide

- Hypotheses 1–4 → the fix is **corrected report options** you re-run and show the user. No data
  change. Explain which option produced the discrepancy.
- Hypotheses 5–6 (customization/accounting-date) → **explain the intended behaviour** with the
  field evidence. Not a bug; not a write.
- Hypothesis 7 (stale sync) → route to the freshness/cron check; the fix is re-running the sync, a
  human/admin action — not editing report figures.
- Hypothesis 8 (a genuinely mis-posted entry) → this is a real accounting correction. It is **out
  of scope for an ORM edit by you**: correcting posted entries touches `button_draft`/`action_post`
  and audit-sound sequencing (see `sequence-journal.md`) and must be done deliberately by an
  accountant with confirmation. Present the evidence and hand off.

Never "fix" a report by editing `account.move.line` amounts directly. Reports are read-outs; if a
figure is wrong the cause is options or a specific entry, and entry corrections follow the
accounting workflow, not raw ORM writes.

## Act

The overwhelmingly common Act here is a **re-read with corrected options**, which is not a write:

```python
corrected = dict(previous_options, date={"mode": "range", "date_from": REAL_FROM,
    "date_to": REAL_TO, "filter": "custom"})
options = call("odoo", {
    "model": "account.report", "method": "get_options",
    "args": [[report["id"]], corrected],
})
data = call("odoo", {
    "model": "account.report", "method": "get_report_information",
    "args": [[report["id"]], options],
})
```

If a genuine entry correction is confirmed and authorized, it belongs to `sequence-journal.md`'s
gated flow, not here.

## Confirm

Show the report re-rendered with the correct options and the figure the user expected, and state
the cause: "the total looked wrong because the report was scoped to <period/company/posted-state>;
with <corrected option> it shows <value>." If the outcome is an explanation (manual tax, rebate,
accounting-date), quote the field values that prove it.

## Escalate / hand off

- **Stale bank/sync data** (cron didn't run) → hand to the admin/ops path to re-run and to enable
  cron failure alerts.
- **A confirmed mis-posted or mis-sequenced entry** → accounting correction via `sequence-journal.md`
  under human authority; do not edit posted moves through the ORM to make a report tie out.
- **Report engine errors** (options/version issues) rather than wrong numbers → inspect
  `get_options`/`get_report_information` per the main `SKILL.md`; if it's a customization/version
  defect, hand off as a code issue.
