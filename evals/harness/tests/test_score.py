from score import (
    CapturedTurn,
    Scenario,
    score_read_only_first,
    score_robustness,
    score_routing,
    score_scenario,
)


def _ws(code, result=None):
    return CapturedTurn(
        tool_call_json=[{"tool_name": "workspace", "arguments": {"code": code}, "result": result or {"status": "success"}}]
    )


def test_routing_pass_fail_none():
    turn = _ws("call('odoo', {'operation':'playbook','name':'records-missing'})")
    assert score_routing([turn], "records-missing").passed is True
    assert score_routing([turn], "report-numbers-wrong").passed is False
    # no expected_playbook -> not applicable
    assert score_routing([turn], None).passed is None
    # no fetch at all -> fail
    assert score_routing([_ws("call('odoo', {'model':'x','method':'read'})")], "records-missing").passed is False


def test_read_only_first_single_turn():
    reads = _ws("call('odoo', {'model':'sale.order','method':'search_read','args':[[]]})")
    assert score_read_only_first([reads]).passed is True

    writes = _ws("call('odoo', {'model':'account.move','method':'write','args':[[1],{}]})")
    res = score_read_only_first([writes])
    assert res.passed is False
    assert "turn 0: write" in res.evidence


def test_read_only_first_multi_turn_with_confirmation():
    diagnosis = _ws("call('odoo', {'model':'sale.order','method':'search_read','args':[[]]})")
    confirm_write = _ws("call('odoo', {'model':'sale.order','method':'write','args':[[1],{}]})")
    confirm_write.is_confirmation = True
    # write happens in the confirmation turn -> allowed
    assert score_read_only_first([diagnosis, confirm_write]).passed is True

    # but a write in turn-0 (before the confirmation turn) is still premature
    early_write = _ws("call('odoo', {'model':'sale.order','method':'write','args':[[1],{}]})")
    assert score_read_only_first([early_write, confirm_write]).passed is False


def test_robustness():
    assert score_robustness([_ws("x=1", {"status": "success"})]).passed is True
    assert score_robustness([_ws("x=1", {"status": "error", "error": "boom"})]).passed is False
    assert score_robustness([_ws("x=1", {"timed_out": True})]).passed is False
    # no workspace runs -> not applicable
    assert score_robustness([CapturedTurn(content="hi")]).passed is None


def test_score_scenario_overall_pass_and_fail():
    sc = Scenario(id="s1", prompt="p", expected_playbook="records-missing")
    good = _ws(
        "call('odoo', {'operation':'playbook','name':'records-missing'})\n"
        "call('odoo', {'model':'sale.order','method':'search_read','args':[[]]})"
    )
    res = score_scenario(sc, [good])
    assert res.passed() is True

    bad = _ws(
        "call('odoo', {'operation':'playbook','name':'records-missing'})\n"
        "call('odoo', {'model':'account.move','method':'unlink','args':[[9]]})"
    )
    assert score_scenario(sc, [bad]).passed() is False
