"""第四次复核：无截止日的旧评价不能当依据；成交量等输入变化不能复用旧结果。"""
from datetime import date
from decimal import Decimal

import pytest

from app.agent.contracts import RequestContext, ToolRejected
from app.agent.service import start_thread
from app.agent.tools import execute_tool
from app.db import connect_admin, transaction
from app.factor_research.service import compare_evaluations, report, start_evaluation
from app.factors.service import create_private


def _wait(owner, eval_id, as_of):
    status = "RUNNING"
    for _ in range(40):
        try:
            status = report(owner, eval_id, as_of)["status"]
        except PermissionError:
            raise
        if status != "RUNNING":
            return status
    return status


def _value(owner, eval_id):
    with transaction(user_id=owner.user_id, read_only=True) as conn:
        row = conn.execute(
            """
            SELECT factor_value FROM research.factor_eval_value
            WHERE eval_id=%s AND stock_code='sh.600001' AND signal_date='2025-05-28' AND horizon=1
            """,
            (eval_id,),
        ).fetchone()
    return None if row is None else Decimal(str(row[0]))


def test_completed_result_without_cutoff_is_not_current_evidence(identities):
    owner, _token = identities[0]
    factor_id = create_private(owner, "有截止日", "close_hfq")
    fresh = start_evaluation(
        owner, factor_id, 1, "SYNTH300", date(2025, 5, 28), date(2025, 5, 30),
        horizons=(1,), as_of=date(2025, 5, 30),
    )
    assert _wait(owner, fresh, date(2025, 5, 30)) == "COMPLETED"
    assert report(owner, fresh, date(2025, 5, 30))["as_of"] == "2025-05-30"
    with transaction(user_id=owner.user_id) as conn:
        old = conn.execute(
            """
            INSERT INTO research.factor_eval_run(
                owner_id, factor_id, version_no, universe, start_date, end_date, horizons,
                idempotency_key, status, snapshot, engine_version, as_of_date, finished_at
            ) VALUES (%s,%s,1,'SYNTH300','2025-05-28','2025-05-30',ARRAY[1,5],'legacy-no-cutoff','COMPLETED','old','d3.3',NULL,now())
            RETURNING eval_id
            """,
            (owner.user_id, factor_id),
        ).fetchone()[0]
    with pytest.raises(PermissionError, match="重新计算"):
        report(owner, old, date(2025, 5, 30))
    with pytest.raises(PermissionError, match="重新计算"):
        compare_evaluations(owner, old, fresh, date(2025, 5, 30))
    thread = start_thread(owner, date(2025, 5, 30), "SYNTH300")
    with transaction(user_id=owner.user_id) as conn:
        turn_id = conn.execute(
            """
            INSERT INTO agent.turn(thread_id, owner_id, client_request_id, status, system_hash, provider, model_name)
            VALUES (%s,%s,'legacy-cutoff','RUNNING','fourth','test','test') RETURNING turn_id
            """,
            (thread, owner.user_id),
        ).fetchone()[0]
    ctx = RequestContext(owner.user_id, date(2025, 5, 30), "SYNTH300", "TEACHING", thread, turn_id)
    with pytest.raises((PermissionError, ToolRejected)):
        execute_tool(owner, ctx, "evaluation_report", {"eval_id": old})
    with pytest.raises((PermissionError, ToolRejected)):
        execute_tool(owner, ctx, "compare_evaluations", {"eval_ids": [old, fresh]})


def test_price_volume_and_membership_changes_are_not_reused(identities):
    owner, _token = identities[0]
    factor_id = create_private(owner, "量价", "amount / volume")
    kwargs = dict(horizons=(1,), as_of=date(2025, 5, 30))
    first = start_evaluation(owner, factor_id, 1, "SYNTH300", date(2025, 5, 28), date(2025, 5, 28), **kwargs)
    assert _wait(owner, first, date(2025, 5, 30)) == "COMPLETED"
    assert start_evaluation(owner, factor_id, 1, "SYNTH300", date(2025, 5, 28), date(2025, 5, 28), **kwargs) == first
    original = _value(owner, first)
    assert original is not None
    with connect_admin() as conn:
        volume, amount = conn.execute(
            "SELECT volume, amount FROM mkt.daily_bar WHERE stock_code='sh.600001' AND trade_date='2025-05-28'"
        ).fetchone()
        member = conn.execute(
            "SELECT index_code, stock_code, valid, source FROM ref.index_member WHERE index_code='SYNTH300' ORDER BY stock_code LIMIT 1"
        ).fetchone()
    try:
        with connect_admin() as conn:
            conn.execute(
                "UPDATE mkt.daily_bar SET volume=volume+1000 WHERE stock_code='sh.600001' AND trade_date='2025-05-28'"
            )
            conn.commit()
        revised = start_evaluation(owner, factor_id, 1, "SYNTH300", date(2025, 5, 28), date(2025, 5, 28), **kwargs)
        assert revised != first
        assert _wait(owner, revised, date(2025, 5, 30)) == "COMPLETED"
        assert _value(owner, revised) != original
        start_evaluation(
            owner, factor_id, 1, "SYNTH300", date(2025, 5, 28), date(2025, 5, 28),
            key="volume-revision-key", **kwargs,
        )
        with connect_admin() as conn:
            conn.execute(
                "UPDATE mkt.daily_bar SET volume=%s, amount=amount+1 WHERE stock_code='sh.600001' AND trade_date='2025-05-28'",
                (volume,),
            )
            conn.commit()
        with pytest.raises(ValueError):
            start_evaluation(
                owner, factor_id, 1, "SYNTH300", date(2025, 5, 28), date(2025, 5, 28),
                key="volume-revision-key", **kwargs,
            )
        after_amount = start_evaluation(owner, factor_id, 1, "SYNTH300", date(2025, 5, 28), date(2025, 5, 28), **kwargs)
        assert after_amount not in {first, revised}
        with connect_admin() as conn:
            conn.execute(
                "UPDATE mkt.daily_bar SET amount=%s WHERE stock_code='sh.600001' AND trade_date='2025-05-28'",
                (amount,),
            )
            conn.execute(
                "UPDATE ref.index_member SET source=%s WHERE index_code=%s AND stock_code=%s AND valid=%s",
                (member[3] + "-rev", member[0], member[1], member[2]),
            )
            conn.commit()
        after_member = start_evaluation(owner, factor_id, 1, "SYNTH300", date(2025, 5, 28), date(2025, 5, 28), **kwargs)
        assert after_member not in {first, revised, after_amount}
    finally:
        with connect_admin() as conn:
            conn.execute(
                "UPDATE mkt.daily_bar SET volume=%s, amount=%s WHERE stock_code='sh.600001' AND trade_date='2025-05-28'",
                (volume, amount),
            )
            conn.execute(
                "UPDATE ref.index_member SET source=%s WHERE index_code=%s AND stock_code=%s AND valid=%s",
                (member[3], member[0], member[1], member[2]),
            )
            conn.commit()
