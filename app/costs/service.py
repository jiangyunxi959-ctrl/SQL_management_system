"""本人费用方案。新版本不覆盖旧版本。"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json

from app.auth.service import Identity
from app.costs.calculator import quote_cn_a, quote_liquidity
from app.db import transaction


def _rate(value, upper):
    number = Decimal(str(value))
    if number < 0 or number > Decimal(str(upper)) or not number.is_finite():
        raise ValueError("费率超出 0 到 0.1，或不是有限数")
    return number


def save_cost(identity: Identity, name: str, spec: dict) -> tuple[int, int]:
    mode = spec.get("mode")
    if mode not in {"CN_A", "CUSTOM_LIQUIDITY"}:
        raise ValueError("费用模式不合法")
    payload = {
        "mode": mode,
        "commission_rate": str(_rate(spec.get("commission_rate", 0), "0.1")),
        "buy_commission_rate": str(_rate(spec.get("buy_commission_rate", spec.get("commission_rate", 0)), "0.1")),
        "sell_commission_rate": str(_rate(spec.get("sell_commission_rate", spec.get("commission_rate", 0)), "0.1")),
        "min_commission": str(_rate(spec.get("min_commission", 0), "1000")),
        "transfer_rate": str(_rate(spec.get("transfer_rate", 0), "0.1")),
        "stamp_rate": str(_rate(spec.get("stamp_rate", 0), "0.1")),
        "maker_rate": str(_rate(spec.get("maker_rate", 0), "0.1")),
        "taker_rate": str(_rate(spec.get("taker_rate", 0), "0.1")),
        "liquidity": spec.get("liquidity", "ALL_TAKER"),
        "slippage_bps": str(_rate(spec.get("slippage_bps", 0), "1000")),
    }
    if payload["liquidity"] not in {"ALL_TAKER", "ALL_MAKER"}:
        raise ValueError("流动性假设不合法")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with transaction(user_id=identity.user_id) as conn:
        row = conn.execute("SELECT profile_id FROM sim.cost_profile WHERE name=%s", (name,)).fetchone()
        if row is None:
            profile_id = conn.execute(
                "INSERT INTO sim.cost_profile(owner_id, name) VALUES (%s, %s) RETURNING profile_id",
                (identity.user_id, name),
            ).fetchone()[0]
            version = 1
        else:
            profile_id = row[0]
            version = conn.execute(
                "SELECT coalesce(max(version_no),0)+1 FROM sim.cost_version WHERE profile_id=%s",
                (profile_id,),
            ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO sim.cost_version(
                profile_id, owner_id, version_no, mode, commission_rate, min_commission, transfer_rate,
                stamp_rate, maker_rate, taker_rate, liquidity, slippage_bps, digest,
                buy_commission_rate, sell_commission_rate
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                profile_id, identity.user_id, version, mode, payload["commission_rate"], payload["min_commission"],
                payload["transfer_rate"], payload["stamp_rate"], payload["maker_rate"], payload["taker_rate"],
                payload["liquidity"], payload["slippage_bps"], digest,
                payload["buy_commission_rate"], payload["sell_commission_rate"],
            ),
        )
    return profile_id, version


def list_costs(identity: Identity):
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        return conn.execute(
            """
            SELECT p.profile_id, p.name, v.version_no, v.mode, v.commission_rate, v.min_commission,
                   v.transfer_rate, v.stamp_rate, v.maker_rate, v.taker_rate, v.liquidity, v.slippage_bps
            FROM sim.cost_profile p
            JOIN sim.cost_version v ON v.profile_id=p.profile_id
            ORDER BY p.profile_id, v.version_no
            """
        ).fetchall()


def quote_for(identity: Identity, profile_id: int, version: int, side: str, amount, raw_price=None):
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        row = conn.execute(
            """
            SELECT mode, commission_rate, min_commission, transfer_rate, stamp_rate, maker_rate, taker_rate, liquidity, slippage_bps
            FROM sim.cost_version WHERE profile_id=%s AND version_no=%s
            """,
            (profile_id, version),
        ).fetchone()
    if row is None:
        raise PermissionError("费用方案不存在或当前不可访问")
    if row[0] == "CN_A":
        return quote_cn_a(side, amount, commission_rate=row[1], min_commission=row[2], transfer_rate=row[3], stamp_rate=row[4], slippage_bps=row[8], raw_price=raw_price)
    return quote_liquidity(side, amount, maker_rate=row[5], taker_rate=row[6], assumption=row[7], slippage_bps=row[8], raw_price=raw_price)
