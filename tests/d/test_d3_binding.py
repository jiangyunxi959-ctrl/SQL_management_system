"""研究日、请求内容和取消发布必须由服务和数据库一起约束。"""
from datetime import date
from decimal import Decimal

import pytest

from app.backtest.factor import create_strategy, run_factor_backtest, wait_backtest
from app.costs.calculator import fixture_rates
from app.costs.service import save_cost
from app.db import transaction
from app.factor_research.service import _publish_evaluation, cancel_eval, report, start_evaluation
from app.factors.service import create_private


def test_evaluation_cannot_pass_the_research_date(identities):
    owner, _token = identities[0]
    factor_id = create_private(owner, "截止日公式", "close_hfq")
    with pytest.raises(ValueError):
        start_evaluation(
            owner, factor_id, 1, "SYNTH300", date(2025, 5, 28), date(2025, 6, 4),
            horizons=(1,), as_of=date(2025, 5, 30),
        )
    with transaction(user_id=owner.user_id, read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM research.factor_eval_run").fetchone()[0] == 0


def test_changed_cash_does_not_reuse_the_old_backtest(identities):
    owner, _token = identities[0]
    factor_id = create_private(owner, "本金公式", "close_hfq")
    strategy_id, version = create_strategy(owner, "本金策略", factor_id, 1, "HIGH", 1, "DAY")
    rates = {"mode": "CN_A", **{key: str(value) for key, value in fixture_rates().items()}}
    profile_id, cost_version = save_cost(owner, "本金费用", rates)
    first = run_factor_backtest(
        owner, strategy_id, version, profile_id, cost_version,
        "SYNTH300", date(2025, 5, 28), date(2025, 5, 30), Decimal("100000"), as_of=date(2025, 5, 30),
    )
    assert wait_backtest(owner, first) == "COMPLETED"
    second = run_factor_backtest(
        owner, strategy_id, version, profile_id, cost_version,
        "SYNTH300", date(2025, 5, 28), date(2025, 5, 30), Decimal("200000"), as_of=date(2025, 5, 30),
    )
    assert second != first
    with pytest.raises(ValueError):
        run_factor_backtest(
            owner, strategy_id, version, profile_id, cost_version,
            "SYNTH300", date(2025, 5, 28), date(2025, 5, 30), Decimal("200000"),
            key="same-request", as_of=date(2025, 5, 30),
        )
        run_factor_backtest(
            owner, strategy_id, version, profile_id, cost_version,
            "SYNTH300", date(2025, 5, 28), date(2025, 5, 30), Decimal("100000"),
            key="same-request", as_of=date(2025, 5, 30),
        )


def test_cancel_is_not_overwritten_by_publish(identities):
    owner, _token = identities[0]
    factor_id = create_private(owner, "取消公式", "close_hfq")
    with transaction(user_id=owner.user_id) as conn:
        eval_id = conn.execute(
            """
            INSERT INTO research.factor_eval_run(
                owner_id, factor_id, version_no, universe, start_date, end_date, horizons,
                idempotency_key, status, snapshot, engine_version
            ) VALUES (%s,%s,1,'SYNTH300','2025-05-28','2025-05-30',ARRAY[1],'cancel-publish','RUNNING','snapshot','d3.3')
            RETURNING eval_id
            """,
            (owner.user_id, factor_id),
        ).fetchone()[0]
    cancel_eval(owner, eval_id)
    assert report(owner, eval_id, date(2025, 5, 30))["status"] == "CANCELLED"
    assert _publish_evaluation(owner, eval_id, [
        (date(2025, 5, 28), "sh.600001", 1, Decimal("1"), date(2025, 5, 29), date(2025, 5, 29), Decimal("0.01"), True, None),
    ], [], []) is False
    assert report(owner, eval_id, date(2025, 5, 30))["status"] == "CANCELLED"
    with transaction(user_id=owner.user_id, read_only=True) as conn:
        assert conn.execute(
            "SELECT count(*) FROM research.factor_eval_value WHERE eval_id=%s",
            (eval_id,),
        ).fetchone()[0] == 0
