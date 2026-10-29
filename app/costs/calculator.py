"""个人费用核算。滑点只体现在成交价，不再从现金另扣一次。"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

MONEY = Decimal("0.01")
BPS = Decimal("0.0001")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class CostQuote:
    commission: Decimal
    transfer: Decimal
    stamp: Decimal
    other: Decimal
    total: Decimal
    price: Decimal
    liquidity: str

    @property
    def components(self) -> dict:
        return {
            "COMMISSION": self.commission,
            "TRANSFER": self.transfer,
            "STAMP": self.stamp,
            "OTHER": self.other,
        }


def execution_price(side: str, raw_price, slippage_bps) -> Decimal:
    price = Decimal(str(raw_price))
    slip = Decimal(str(slippage_bps)) * BPS
    if side == "B":
        return money(price * (1 + slip))
    if side == "S":
        return money(price * (1 - slip))
    raise ValueError("买卖方向不合法")


def quote_cn_a(side: str, amount, *, commission_rate, min_commission, transfer_rate, stamp_rate, slippage_bps=0, raw_price=None) -> CostQuote:
    if side not in {"B", "S"}:
        raise ValueError("买卖方向不合法")
    amount = Decimal(str(amount))
    if amount < 0:
        raise ValueError("成交金额不能为负")
    price = execution_price(side, raw_price, slippage_bps) if raw_price is not None else Decimal("0")
    if amount == 0:
        return CostQuote(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), price, "CN_A")
    commission = money(max(amount * Decimal(str(commission_rate)), Decimal(str(min_commission))))
    transfer = money(amount * Decimal(str(transfer_rate)))
    stamp = money(amount * Decimal(str(stamp_rate))) if side == "S" else Decimal("0.00")
    total = commission + transfer + stamp
    return CostQuote(commission, transfer, stamp, Decimal("0.00"), total, price, "CN_A")


def quote_liquidity(side: str, amount, *, maker_rate, taker_rate, assumption="ALL_TAKER", slippage_bps=0, raw_price=None) -> CostQuote:
    if assumption not in {"ALL_TAKER", "ALL_MAKER"}:
        raise ValueError("流动性假设只能是全部 taker 或全部 maker")
    rate = Decimal(str(taker_rate if assumption == "ALL_TAKER" else maker_rate))
    amount = Decimal(str(amount))
    fee = Decimal("0.00") if amount == 0 else money(amount * rate)
    price = execution_price(side, raw_price, slippage_bps) if raw_price is not None else Decimal("0")
    return CostQuote(fee, Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), fee, price, assumption)


def fixture_rates():
    return {
        "commission_rate": Decimal("0.0003"),
        "min_commission": Decimal("5"),
        "transfer_rate": Decimal("0.00001"),
        "stamp_rate": Decimal("0.0005"),
    }
