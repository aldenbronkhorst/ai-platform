"""F4 + F7: the golden gate actually scores (not 0/0), and write-gate scenarios
are exercised across a confirmation turn."""
import os

from run_evals import load_scenarios, run_offline
from score import Scenario

_HERE = os.path.dirname(os.path.abspath(__file__))
_GOLDEN_SCENARIOS = os.path.normpath(os.path.join(_HERE, "..", "..", "troubleshooting", "golden_scenarios.jsonl"))
_GOLDEN_TRANSCRIPTS = os.path.normpath(os.path.join(_HERE, "..", "fixtures", "golden"))


def _run():
    scenarios = load_scenarios(_GOLDEN_SCENARIOS)
    results = run_offline(scenarios, _GOLDEN_TRANSCRIPTS, judge_fn=None)
    return scenarios, {r.scenario_id: r for r in results}, results


def test_golden_set_scores_and_is_not_a_no_op():
    scenarios, _, results = _run()
    # every golden scenario has a transcript and is scored -> the gate is NOT 0/0
    assert len(results) == len(scenarios) == 10
    assert all(r.passed() for r in results), [r.scenario_id for r in results if not r.passed()]
    for r in results:
        assert r.by_axis()["routing"].passed is True, r.scenario_id


def test_write_gate_scenarios_pass_via_confirmation_turn():
    _, by_id, _ = _run()
    # both write-gate scenarios write only in the confirmation (2nd) turn
    for sid in ("resequence-write-gate", "duplicate-customers-merge"):
        assert by_id[sid].by_axis()["read_only_first"].passed is True, sid


def test_known_answer_conclusion_is_deterministic_without_judge():
    _, by_id, _ = _run()
    assert by_id["product-reference-gaps"].by_axis()["conclusion"].passed is True


def test_scenario_parses_followups():
    sc = Scenario.from_dict({
        "id": "x", "prompt": "do it",
        "followups": [{"prompt": "yes, apply it", "is_confirmation": True}],
    })
    assert len(sc.followups) == 1
    assert sc.followups[0]["is_confirmation"] is True
