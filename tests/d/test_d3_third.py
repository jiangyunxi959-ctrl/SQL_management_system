"""第三次复核：早日期不能读取晚日期评价；持仓跨除权不能发布完成收益。"""
from datetime import date
from decimal import Decimal

import pytest

from app.agent.contracts import RequestContext, ToolRejected
from app.agent.service import start_thread
from app.agent.tools import execute_tool
from app.backtest.factor import create_strategy, run_factor_backtest, wait_backtest
from app.costs.calculator import fixture_rates
from app.costs.service import save_cost
from app.db import transaction
from app.factor_research.service import compare_evaluations, report, start_evaluation
from app.factors.service import create_private


def _turn(identity, as_of):
    thread = start_thread(identity, as_of, "SYNTH300")
    with transaction(user_id=identity.user_id) as conn:
        turn_id = conn.execute(
            """
            INSERT INTO agent.turn(thread_id, owner_id, client_request_id, status, system_hash, provider, model_name)
            VALUES (%s,%s,%s,'RUNNING','third-review','test','test')
            RETURNING turn_id
            """,
            (thread, identity.user_id, f"third{as_of.strftime('%m%d')}{identity.user_id}"),
        ).fetchone()[0]
    return RequestContext(identity.user_id, as_of, "SYNTH300", "TEACHING", thread, turn_id)


def test_early_date_cannot_read_or_compare_a_later_evaluation(identities):
    owner, _token = identities[0]
    factor_id = create_private(owner, "晚日期收盘", "close_hfq")
    late = start_evaluation(
        owner, factor_id, 1, "SYNTH300", date(2025, 6, 2), date(2025, 6, 4),
        horizons=(1,), as_of=date(2025, 6, 4),
    )
    early = start_evaluation(
        owner, factor_id, 1, "SYNTH300", date(2025, 5, 28), date(2025, 5, 30),
        horizons=(1, 5), as_of=date(2025, 5, 30),
    )
    from app.factor_research.service import report as wait_report
    for eval_id, day in ((late, date(2025, 6, 4)), (early, date(2025, 5, 30))):
        status = "RUNNING"
        for _ in range(40):
            status = wait_report(owner, eval_id, day)["status"]
            if status != "RUNNING":
                break
        assert status == "COMPLETED"
    with pytest.raises(PermissionError):
        report(owner, late, date(2025, 5, 30))
    with pytest.raises(PermissionError):
        compare_evaluations(owner, late, early, date(2025, 5, 30))
    with pytest.raises(ValueError):
        compare_evaluations(owner, late, early, date(2025, 6, 4))
    ctx = _turn(owner, date(2025, 5, 30))
    with pytest.raises((PermissionError, ToolRejected)):
        execute_tool(owner, ctx, "evaluation_report", {"eval_id": late})
    with pytest.raises((PermissionError, ToolRejected)):
        execute_tool(owner, ctx, "compare_evaluations", {"eval_ids": [late, early]})


def test_holding_across_corporate_action_is_not_completed(identities):
    owner, _token = identities[0]
    factor_id = create_private(owner, "低价收盘", "close_raw")
    strategy_id, version = create_strategy(owner, "低价持有", factor_id, 1, "LOW", 1, "DAY")
    rates = {"mode": "CN_A", **{key: str(value) for key, value in fixture_rates().items()}}
    profile_id, cost_version = save_cost(owner, "除权费用", rates)
    bt_id = run_factor_backtest(
        owner, strategy_id, version, profile_id, cost_version,
        "SYNTH300", date(2025, 6, 12), date(2025, 6, 17), Decimal("1000000"), as_of=date(2025, 6, 17),
    )
    assert wait_backtest(owner, bt_id) == "FAILED"
    with transaction(user_id=owner.user_id, read_only=True) as conn:
        error = conn.execute("SELECT error_class FROM research.factor_bt_run WHERE bt_id=%s", (bt_id,)).fetchone()[0]
        points = conn.execute("SELECT count(*) FROM research.backtest_pnl WHERE bt_id=%s", (bt_id,)).fetchone()[0]
    assert "除权" in error
    assert points == 0
