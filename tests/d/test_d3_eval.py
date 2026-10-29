"""短区间评价：标签晚于信号日，另一用户读不到。"""
from datetime import date
import time

import pytest

from decimal import Decimal

from app.backtest.factor import create_strategy, run_factor_backtest, wait_backtest
from app.costs.calculator import fixture_rates
from app.costs.service import save_cost
from app.db import transaction
from app.factor_research.service import report, start_evaluation
from app.factors.service import create_private


def test_evaluation_finishes_and_stays_private(identities):
    owner, _token = identities[0]
    other, _other_token = identities[1]
    factor_id = create_private(owner, "评价用收盘", "close_hfq")
    eval_id = start_evaluation(
        owner, factor_id, 1, "SYNTH300", date(2025, 5, 28), date(2025, 5, 30), horizons=(1,), as_of=date(2025, 5, 30),
    )
    status = "RUNNING"
    data = None
    for _ in range(40):
        data = report(owner, eval_id, date(2025, 5, 30))
        status = data["status"]
        if status != "RUNNING":
            break
        time.sleep(0.5)
    assert status == "COMPLETED", data
    assert data["days"]
    with pytest.raises(PermissionError):
        report(other, eval_id, date(2025, 5, 30))


def test_restart_marks_unfinished_evaluation(identities):
    import app.factor_research.service as research
    owner, _token = identities[0]
    factor_id = create_private(owner, "中断用收盘", "close_hfq")
    with transaction(user_id=owner.user_id) as conn:
        eval_id = conn.execute(
            """
            INSERT INTO research.factor_eval_run(
                owner_id, factor_id, version_no, universe, start_date, end_date, horizons,
                idempotency_key, status, snapshot, worker_id
            ) VALUES (%s,%s,1,'SYNTH300','2025-05-28','2025-05-30',ARRAY[1],'restart-test','RUNNING','snapshot','worker-under-test')
            RETURNING eval_id
            """,
            (owner.user_id, factor_id),
        ).fetchone()[0]
    assert research.interrupt_worker("worker-under-test") >= 1
    assert report(owner, eval_id, date(2025, 5, 30))["status"] == "INTERRUPTED"
    assert research.interrupt_worker("another-process") == 0


def test_backtest_trades_after_signal(identities):
    owner, _token = identities[0]
    factor_id = create_private(owner, "回测用收盘", "close_hfq")
    strategy_id, strategy_version = create_strategy(owner, "短回测", factor_id, 1, "HIGH", 1, "DAY")
    rates = {"mode": "CN_A", **{key: str(value) for key, value in fixture_rates().items()}}
    profile_id, cost_version = save_cost(owner, "回测费用", rates)
    bt_id = run_factor_backtest(
        owner, strategy_id, strategy_version, profile_id, cost_version,
        "SYNTH300", date(2025, 5, 28), date(2025, 5, 30), Decimal("100000"),
    )
    assert wait_backtest(owner, bt_id) == "COMPLETED"
    with transaction(user_id=owner.user_id, read_only=True) as conn:
        trades = conn.execute(
            "SELECT signal_date, trade_date, status, raw_price, price, qty, amount, fee FROM research.factor_trade WHERE bt_id=%s",
            (bt_id,),
        ).fetchall()
        points = conn.execute(
            "SELECT trade_date, net_equity, gross_same_fills, cumulative_fees FROM research.backtest_pnl WHERE bt_id=%s ORDER BY trade_date",
            (bt_id,),
        ).fetchall()
    assert points
    assert any(row[2] == "FILLED" for row in trades)
    for signal, traded, *_rest in trades:
        assert traded > signal
    assert points[-1][0] == date(2025, 5, 30)
    last = points[-1]
    assert last[2] == last[1] + last[3]
    for _signal, _traded, status, _raw_price, price, qty, amount, _fee in trades:
        if status == "FILLED":
            assert amount == price * qty
