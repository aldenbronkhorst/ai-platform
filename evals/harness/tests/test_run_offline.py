import os

from run_evals import load_scenarios, run_offline

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures")


def test_run_offline_scores_mini_set():
    scenarios = load_scenarios(os.path.join(FIX, "mini_scenarios.jsonl"))
    results = run_offline(scenarios, os.path.join(FIX, "transcripts"), judge_fn=None)
    by_id = {r.scenario_id: r for r in results}

    # clean run: right playbook, read-only, workspace ok
    assert by_id["good-routing-readonly"].passed() is True
    assert by_id["good-routing-readonly"].by_axis()["routing"].passed is True
    assert by_id["good-routing-readonly"].by_axis()["read_only_first"].passed is True

    # wrote during diagnosis -> must fail on read-only-first even though routing was right
    assert by_id["premature-write"].by_axis()["routing"].passed is True
    assert by_id["premature-write"].by_axis()["read_only_first"].passed is False
    assert by_id["premature-write"].passed() is False

    # deterministic conclusion from known_answer, no judge model needed
    assert by_id["known-answer"].by_axis()["conclusion"].passed is True
    assert by_id["known-answer"].passed() is True
