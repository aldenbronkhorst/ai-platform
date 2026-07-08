from judge import deterministic_conclusion, judge_conclusion
from score import AxisResult, Scenario, ScenarioResult
from scorecard import build_scorecard, render_scorecard


def test_deterministic_known_answer_pass_and_fail():
    sc = Scenario(id="k", prompt="how many", known_answer={"total": 3461, "uncoded": 185, "archived": 0})
    ok = deterministic_conclusion(sc, "All 3,461 products intact; 185 uncoded.")
    assert ok.passed is True
    bad = deterministic_conclusion(sc, "About 3000 products, some uncoded.")
    assert bad.passed is False


def test_no_known_answer_returns_none():
    sc = Scenario(id="x", prompt="p")
    assert deterministic_conclusion(sc, "anything") is None


def test_judge_conclusion_uses_stub_when_no_known_answer():
    sc = Scenario(id="x", prompt="p", expected_behaviors=["explain root cause"])
    calls = {}

    def stub(prompt):
        calls["prompt"] = prompt
        return {"passed": True, "rationale": "looks right"}

    res = judge_conclusion(sc, "the root cause is a filter", judge_fn=stub)
    assert res.passed is True
    assert "EXPECTED BEHAVIOURS" in calls["prompt"]


def test_judge_conclusion_none_without_judge():
    sc = Scenario(id="x", prompt="p")
    assert judge_conclusion(sc, "ans", judge_fn=None).passed is None


def test_scorecard_counts_and_renders():
    results = [
        ScenarioResult("a", [AxisResult("routing", True), AxisResult("read_only_first", True),
                             AxisResult("robustness", True), AxisResult("conclusion", True)]),
        ScenarioResult("b", [AxisResult("routing", False), AxisResult("read_only_first", True),
                             AxisResult("robustness", None), AxisResult("conclusion", None)]),
    ]
    card = build_scorecard(results)
    assert card["scenarios"] == 2
    assert card["overall_pass"] == 1                       # b failed on routing
    assert card["per_axis"]["routing"] == {"pass": 1, "scored": 2, "skipped": 0}
    assert card["per_axis"]["robustness"] == {"pass": 1, "scored": 1, "skipped": 1}
    text = render_scorecard(results, card)
    assert "Overall: 1/2" in text
    assert "b: routing" in text
