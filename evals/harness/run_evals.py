"""One-command troubleshooting eval runner.

Modes:
  live     — drive the streaming chat endpoint on a deployment, score each turn.
  offline  — score pre-recorded transcripts (no network / no creds), for TDD,
             regression fixtures, and re-scoring after a rubric change.

Examples:
  # offline, against recorded transcripts (one <scenario_id>.json per scenario)
  python evals/harness/run_evals.py --offline --transcripts evals/harness/fixtures/transcripts

  # live against staging (needs the staging api-key secret + a live Odoo connector
  # + a configured model route). Judge uses Claude if ANTHROPIC_API_KEY is set.
  BASE_URL=https://<staging> API_KEY=<kv:api-key> ANTHROPIC_API_KEY=<key> \
      python evals/harness/run_evals.py --live

Transcript file format (offline): either a list of `message.complete` payloads,
or {"turns": [{content, tool_call_json, token_usage, is_confirmation}, ...]}.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from judge import claude_judge_fn, judge_conclusion  # noqa: E402
from score import CapturedTurn, Scenario, score_scenario  # noqa: E402
from scorecard import render_scorecard  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_SCENARIOS = os.path.normpath(
    os.path.join(_HERE, "..", "troubleshooting", "golden_scenarios.jsonl")
)


def load_scenarios(path: str) -> list[Scenario]:
    scenarios: list[Scenario] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                scenarios.append(Scenario.from_dict(json.loads(line)))
    return scenarios


def _turns_from_transcript(obj) -> list[CapturedTurn]:
    if isinstance(obj, dict) and "turns" in obj:
        return [
            CapturedTurn(
                content=t.get("content", ""),
                tool_call_json=t.get("tool_call_json") or [],
                token_usage=t.get("token_usage"),
                is_confirmation=bool(t.get("is_confirmation")),
            )
            for t in obj["turns"]
        ]
    if isinstance(obj, list):  # list of message.complete payloads
        return [
            CapturedTurn(
                content=m.get("content", ""),
                tool_call_json=m.get("tool_call_json") or [],
                token_usage=m.get("token_usage_json"),
            )
            for m in obj
        ]
    raise ValueError("unrecognized transcript format")


def run_offline(scenarios: list[Scenario], transcripts_dir: str, judge_fn):
    results = []
    for sc in scenarios:
        path = os.path.join(transcripts_dir, f"{sc.id}.json")
        if not os.path.exists(path):
            print(f"  skip {sc.id}: no transcript at {path}")
            continue
        with open(path, "r", encoding="utf-8") as fh:
            turns = _turns_from_transcript(json.load(fh))
        conclusion = judge_conclusion(sc, turns[-1].content if turns else "", judge_fn)
        results.append(score_scenario(sc, turns, conclusion))
    return results


def run_live(scenarios: list[Scenario], base_url: str, api_key: str, judge_fn):
    from sse import capture_turn, create_session  # local import: needs httpx

    results = []
    for sc in scenarios:
        print(f"  running {sc.id} ...", flush=True)
        session_id = create_session(base_url, api_key)
        turns = [capture_turn(base_url, api_key, session_id, sc.prompt)]
        for followup in sc.followups:  # multi-turn (e.g. write-gate: confirm-then-write)
            turn = capture_turn(base_url, api_key, session_id, followup.get("prompt", ""))
            turn.is_confirmation = bool(followup.get("is_confirmation"))
            turns.append(turn)
        conclusion = judge_conclusion(sc, turns[-1].content, judge_fn)
        results.append(score_scenario(sc, turns, conclusion))
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Troubleshooting eval runner")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="drive the live chat stream")
    mode.add_argument("--offline", action="store_true", help="score recorded transcripts")
    ap.add_argument("--scenarios", default=_DEFAULT_SCENARIOS)
    ap.add_argument("--transcripts", help="dir of <id>.json transcripts (offline mode)")
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL"))
    ap.add_argument("--api-key", default=os.environ.get("API_KEY"))
    ap.add_argument("--no-judge", action="store_true", help="skip the model judge (deterministic only)")
    ap.add_argument("--min-pass-rate", type=float, default=0.0,
                    help="exit non-zero if overall pass rate is below this (0-1)")
    ap.add_argument("--require-conclusion", action="store_true",
                    help="fail a scenario whose 'correct conclusion' axis was not scored "
                         "(no judge / no known_answer), so an unjudged run cannot pass the gate")
    args = ap.parse_args(argv)

    scenarios = load_scenarios(args.scenarios)

    judge_fn = None
    if not args.no_judge and os.environ.get("ANTHROPIC_API_KEY"):
        judge_fn = claude_judge_fn()

    if args.live:
        if not args.base_url or not args.api_key:
            ap.error("--live needs --base-url/BASE_URL and --api-key/API_KEY")
        results = run_live(scenarios, args.base_url, args.api_key, judge_fn)
    else:
        if not args.transcripts:
            ap.error("--offline needs --transcripts <dir>")
        results = run_offline(scenarios, args.transcripts, judge_fn)

    print(render_scorecard(results))

    def _passes_gate(r) -> bool:
        if not r.passed():
            return False
        if args.require_conclusion:
            concl = r.by_axis().get("conclusion")
            if concl is None or concl.passed is not True:
                return False
        return True

    n = len(results)
    rate = (sum(1 for r in results if _passes_gate(r)) / n) if n else 0.0
    if rate < args.min_pass_rate:
        print(f"\nFAIL: pass rate {rate:.0%} < required {args.min_pass_rate:.0%}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
