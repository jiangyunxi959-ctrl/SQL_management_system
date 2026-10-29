"""D3 行权限：换用户、无上下文和伪造父对象都由数据库拒绝。"""
from decimal import Decimal

import pytest

from app.costs.service import quote_for, save_cost
from app.db import transaction
from app.factors.service import create_private


RATES = {
    "mode": "CN_A",
    "commission_rate": "0.0003",
    "min_commission": "5",
    "transfer_rate": "0.00001",
    "stamp_rate": "0.0005",
    "slippage_bps": "0",
}


def test_other_user_and_missing_context_cannot_read_cost(identities):
    owner, _token = identities[0]
    other, _other_token = identities[1]
    profile_id, version = save_cost(owner, "甲的费用", RATES)
    quoted = quote_for(owner, profile_id, version, "B", 1000, raw_price=10)
    assert quoted.total == Decimal("5.01")
    with pytest.raises(PermissionError):
        quote_for(other, profile_id, version, "B", 1000, raw_price=10)
    with transaction(read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM sim.cost_profile").fetchone()[0] == 0
    with pytest.raises(Exception):
        with transaction() as conn:
            conn.execute(
                "INSERT INTO sim.cost_profile(owner_id, name) VALUES (%s, '无上下文')",
                (owner.user_id,),
            )


def test_saved_cost_version_cannot_be_updated(identities):
    owner, _token = identities[0]
    profile_id, version = save_cost(owner, "冻结费用", RATES)
    with pytest.raises(Exception):
        with transaction(user_id=owner.user_id) as conn:
            conn.execute(
                "UPDATE sim.cost_version SET slippage_bps=0 WHERE profile_id=%s AND version_no=%s",
                (profile_id, version),
            )


def test_forged_parent_is_rejected(identities):
    owner, _token = identities[0]
    other, _other_token = identities[1]
    factor_id = create_private(owner, "甲的公式", "close_hfq")
    version = 1
    with transaction(user_id=owner.user_id) as conn:
        strategy_id = conn.execute(
            "INSERT INTO research.factor_strategy(owner_id, name) VALUES (%s, '甲的策略') RETURNING strategy_id",
            (owner.user_id,),
        ).fetchone()[0]
    with pytest.raises(Exception):
        with transaction(user_id=other.user_id) as conn:
            conn.execute(
                """
                INSERT INTO research.factor_strategy_version(
                    strategy_id, owner_id, version_no, factor_id, factor_version, direction, top_n, rebalance, digest
                ) VALUES (%s,%s,1,%s,%s,'HIGH',5,'DAY','forged')
                """,
                (strategy_id, other.user_id, factor_id, version),
            )
