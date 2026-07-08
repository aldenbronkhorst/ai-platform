"""The judged 5th axis: "correct conclusion".

Two-tier, per the effectiveness framework:
  1. Deterministic short-circuit when the scenario carries a structured
     `known_answer` (only product-reference-gaps does today) — check its salient
     numbers appear in the final answer.
  2. Otherwise call a judge model (Claude, out-of-band from the platform so the
     eval score is not coupled to the routing being tested) with the scenario's
     expected/forbidden behaviours and the captured answer.

`judge_fn` is injected so the whole module is unit-testable offline with a stub.
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable

from score import AxisResult, Scenario

# a judge fn takes the built prompt and returns {"passed": bool, "rationale": str}
JudgeFn = Callable[[str], dict]


def _salient_numbers(known_answer: dict) -> list[str]:
    # ignore trivial values (0/1) whose bare digits match almost any answer
    out = []
    for v in known_answer.values():
        if isinstance(v, bool):
            continue
        if isinstance(v, int) and abs(v) >= 10:
            out.append(str(v))
    return out


def deterministic_conclusion(scenario: Scenario, answer: str) -> AxisResult | None:
    ka = scenario.known_answer
    if not ka:
        return None
    salient = _salient_numbers(ka)
    if not salient:
        return None
    packed = re.sub(r"[,\s]", "", answer)  # tolerate thousands separators
    missing = [n for n in salient if n not in packed]
    if not missing:
        return AxisResult("conclusion", True, f"answer contains known_answer numbers {salient}")
    return AxisResult("conclusion", False, f"answer missing known_answer values {missing}")


def build_judge_prompt(scenario: Scenario, answer: str) -> str:
    expected = "\n".join(f"  - {b}" for b in scenario.expected_behaviors) or "  (none listed)"
    forbidden = "\n".join(f"  - {b}" for b in scenario.forbidden_behaviors) or "  (none listed)"
    ka = json.dumps(scenario.known_answer) if scenario.known_answer else "(none)"
    return (
        "You are grading whether an AI assistant reached the CORRECT CONCLUSION for an "
        "Odoo troubleshooting scenario. Judge only the substance of the final answer.\n\n"
        f"SYMPTOM: {scenario.symptom}\n"
        f"USER PROMPT: {scenario.prompt}\n\n"
        f"EXPECTED BEHAVIOURS (the answer should reflect these):\n{expected}\n\n"
        f"FORBIDDEN BEHAVIOURS (the answer must NOT do these):\n{forbidden}\n\n"
        f"KNOWN ANSWER (ground truth, if any): {ka}\n\n"
        f"ASSISTANT FINAL ANSWER:\n\"\"\"\n{answer}\n\"\"\"\n\n"
        "Reply with ONLY a JSON object: {\"passed\": true|false, \"rationale\": \"one sentence\"}. "
        "passed=true iff the conclusion is substantively correct and violates no forbidden behaviour."
    )


def judge_conclusion(scenario: Scenario, answer: str, judge_fn: JudgeFn | None = None) -> AxisResult:
    det = deterministic_conclusion(scenario, answer)
    if det is not None:
        return det
    if judge_fn is None:
        return AxisResult("conclusion", None,
                          "no judge configured (set ANTHROPIC_API_KEY or pass judge_fn)")
    verdict = judge_fn(build_judge_prompt(scenario, answer))
    return AxisResult(
        "conclusion",
        bool(verdict.get("passed")),
        str(verdict.get("rationale", ""))[:300],
    )


def claude_judge_fn(model: str | None = None) -> JudgeFn:
    """Live judge backed by the Claude API. Requires ANTHROPIC_API_KEY in env.

    Kept out of the deterministic path so unit tests never need network/keys.
    """
    model = model or os.environ.get("EVAL_JUDGE_MODEL", "claude-sonnet-5")

    def _fn(prompt: str) -> dict:
        import anthropic  # lazy: only needed for a live run

        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {"passed": False, "rationale": text[:200]}
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {"passed": False, "rationale": text[:200]}

    return _fn
