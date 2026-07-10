# Troubleshooting eval harness

Turns "10 prompts I paste by hand" into **one command, one pass-rate scorecard**
for the Odoo-troubleshooting guidance. It drives the streaming chat endpoint,
scores three rubric axes deterministically from the captured trace, and hands the
"correct conclusion" axis to a judge model.

This is the pre-deploy **regression gate**: run it on staging before/after any
guidance change (`SKILL.md` / playbooks); the pass rate must hold or improve.

## The one thing that shapes the whole design

The model has **no first-class `odoo` tool**. `CANONICAL_TOOL_DEFINITIONS` exposes
only `workspace` / `document_reader`. The model reaches
Odoo by writing Python that calls `call('odoo', {...})` **inside the `workspace`
tool**. So a `message.complete.tool_call_json` entry is `tool_name:'workspace'`
with the Odoo operation/method **buried in `arguments.code`**.

Consequences the harness lives with:
- We recover Odoo calls by **parsing the Workspace code** (`extract.py`, AST-first,
  regex fallback). Fully-literal calls are recovered exactly; calls built from
  variables/f-strings are flagged `dynamic` and scored conservatively (never a
  silent pass).
- Writes are classified by a **read-allowlist** (default-deny), so workflow/wizard
  mutations (`action_post`, `button_draft`, `*_confirm`, resequence, ...) count as
  writes even though they aren't `create`/`write`/`unlink`.
- Score off **`message.complete.tool_call_json`**, never the live `activity`/`tool.*`
  spans — those are redaction-scrubbed (playbook `name` and batch `calls` are
  dropped on the wire).

The durable fix is structured per-call broker telemetry ("Track B"); until then,
code-parsing is the most reliable signal available.

## Axes

| Axis | How scored | Source |
|---|---|---|
| Routing | expected playbook fetched? (`operation:playbook`, `name`) | deterministic |
| Read-only-first | any write before a confirmation turn? | deterministic |
| Robustness | every workspace run `status:success`? | deterministic |
| Conclusion | correct answer vs `known_answer` / expected behaviours | known_answer check, else judge model |

`passed` is `True` / `False` / `None`. **`None` = not applicable / could not be
decided deterministically** (e.g. no `expected_playbook`, an unparseable Odoo
call, or the judge was off). It is surfaced but never counted as a failure.

## Run it — offline (now, no creds)

```bash
# from the repo root; any Python 3.11+ (the modules are pure stdlib)
python -m pytest evals/harness/tests -p no:cacheprovider -q          # 23 tests

# score the bundled synthetic transcripts and print a scorecard
python evals/harness/run_evals.py --offline \
  --scenarios evals/harness/fixtures/mini_scenarios.jsonl \
  --transcripts evals/harness/fixtures/transcripts
```

Offline mode scores pre-recorded transcripts (`<scenario_id>.json`, either a list
of `message.complete` payloads or `{"turns":[...]}`). Use it for TDD, for
re-scoring after a rubric change, and — once you can reach staging — as a
regression fixture by committing one real transcript per scenario.

## Run it — live (needs staging)

```bash
BASE_URL=https://<staging-host> \
API_KEY=<staging 'api-key' Key Vault secret> \
ANTHROPIC_API_KEY=<key for the judge> \
python evals/harness/run_evals.py --live --min-pass-rate 0.9
```

- **Endpoint:** `POST /chat/sessions` then `POST /chat/sessions/{id}/messages/stream`
  (note the `/chat` prefix). Auth = header `X-API-Key`.
- **Preconditions on staging:** a populated `API_KEY` secret, a **live + credentialed
  Odoo connector** (`operation:playbook` returns "not configured" otherwise), and a
  **configured model route** for general chat (else 502). Consume `heartbeat` events
  and wait for `done`.
- **Judge:** if `ANTHROPIC_API_KEY` is set, "conclusion" uses Claude out-of-band
  (`EVAL_JUDGE_MODEL`, default `claude-sonnet-5`); otherwise it is skipped.

## Honest limits

- **10 scenarios is a smoke test**, not statistical confidence — grow toward 30-50.
- Only **1/10** scenarios has a machine-checkable `known_answer` (product-reference-gaps,
  3461/185); the rest need the judge, which has its own error rate — spot-check a sample.
- **Write-gate** is only a *convention* server-side (no enforced gate). The harness
  measures "no write before confirmation" from the trace, but truly exercising the
  gate needs **two-turn scenarios** (diagnose, then confirm). Single-prompt scenarios
  only prove "did not write during diagnosis".
- The same code-parsing means a write built from **dynamic args** is flagged
  (`None`, not a pass) rather than classified — again, Track B (broker telemetry)
  is the real fix, and it's what makes these axes objective **in production** too.

## Files

- `extract.py` — recover Odoo calls from Workspace code; read/write classification.
- `score.py` — scenario/turn types + the three deterministic axis scorers.
- `judge.py` — deterministic `known_answer` check + pluggable Claude judge.
- `sse.py` — SSE frame parser (pure) + live `create_session`/`capture_turn`.
- `scorecard.py` — aggregate to a pass-rate table.
- `run_evals.py` — CLI (`--offline` / `--live`).
- `tests/`, `fixtures/` — offline unit + integration coverage.
