# Troubleshooting golden scenarios

Behavioural evals for the Odoo troubleshooting guidance. They answer the question the unit tests
can't: *does the model, given the guidance, actually drive the platform correctly on realistic
troubleshooting tasks?*

## Why these aren't unit tests

The runtime is covered by deterministic tests in
`apps/ai-core-api/tests/test_workspace_runtime.py` (does the engine execute code, enforce timeouts,
persist files, etc.). Those don't tell you whether the **model** writes working, correct, safe code
— which is where real failures live (e.g. the Workspace statelessness `NameError`). That needs a
live model, so these scenarios run against a deployed environment and are scored by a human or a
judge model against `RUBRIC.md`. They are intentionally **not** wired into CI.

## What's here

- `golden_scenarios.jsonl` — one scenario per line: `prompt`, `expected_playbook`,
  `expected_behaviors`, `forbidden_behaviors`, optional `known_answer`, `tags`.
- `RUBRIC.md` — the five-axis scoring (routing, read-only-first, write-gate, conclusion,
  no-forbidden) and how to interpret results.

## How to run

1. Use a **staging** environment (or a non-destructive prod account) with Odoo connected. Several
   scenarios would otherwise attempt writes if the model misbehaves — the point is to confirm it
   asks first, but run somewhere safe.
2. For each line, start a fresh chat and paste the `prompt`.
3. Capture the transcript, including tool/activity calls (look for `odoo` calls with
   `operation: playbook`).
4. Score against `RUBRIC.md`. A scenario passes at 5/5.
5. Record the pass rate. Re-run the whole set before and after any guidance change; the rate must
   not drop.

A light harness can automate steps 2-4 via the platform API and a judge-model pass over each
transcript, but start by running them by hand — the transcripts are the most informative artifact.

## Ground truth captured so far

- `product-reference-gaps` / `multi-step-analysis-regression`: 3,461 products, all active, 0
  archived, 185 without an internal reference. Gaps are expected (multiple coding schemes +
  non-transactional sequence), not lost products. Verified 8 Jul 2026 from a full export.

## Adding scenarios

Every confirmed production issue should become a scenario here — that's the resolution loop in
practice. Keep prompts in the user's real voice, list the concrete `expected_behaviors` and
`forbidden_behaviors`, and record a `known_answer` whenever you have verified facts.
