from decimal import Decimal

from app.backtest.factor import round_trip_cash
from app.costs.calculator import fixture_rates, quote_cn_a, quote_liquidity
from app.factor_research.metrics import assign_groups, official_ic, rank_ic


def _fixture(**extra):
    base = fixture_rates()
    base.update(extra)
    return base


def test_perfect_and_reverse_ic():
    factors = list(range(1, 11))
    labels = [Decimal(i) / 100 for i in range(1, 11)]
    ic, rank, reason = official_ic(factors, labels)
    assert reason is None
    assert ic == Decimal(1)
    assert rank == Decimal(1)
    reverse_ic, reverse_rank, reverse_reason = official_ic(factors, list(reversed(labels)))
    assert reverse_reason is None
    assert reverse_ic == Decimal(-1)
    assert reverse_rank == Decimal(-1)


def test_zero_variance_ties_and_short_sample():
    ic, _rank, reason = official_ic([1] * 10, list(range(10)))
    assert ic is None and reason == "ZERO_VARIANCE"
    short, _rank, short_reason = official_ic(list(range(9)), list(range(9)))
    assert short is None and short_reason == "INSUFFICIENT_SAMPLE"
    tied, _reason = rank_ic([1, 1, 2], [3, 1, 2])
    assert tied is not None


def test_nineteen_names_use_three_groups():
    groups, reason = assign_groups([(f"s{i:02d}", Decimal(i)) for i in range(19)])
    assert reason is None
    assert len(groups) == 3
    assert all(group["count"] >= 5 for group in groups)


def test_equal_values_are_not_split():
    pairs = [(f"s{i:02d}", Decimal(i if i < 10 else 10)) for i in range(25)]
    groups, reason = assign_groups(pairs)
    assert reason == "DOWNGRADED_TO_3"
    tied = [group for group in groups if any(stock.startswith("s") and pairs[int(stock[1:])][1] == 10 for stock in group["stocks"])]
    assert len(tied) == 1
    assert tied[0]["count"] == 15


def test_fee_fixture_numbers():
    rates = _fixture()
    buy = quote_cn_a("B", 10000, **rates)
    sell = quote_cn_a("S", 10000, **rates)
    assert (buy.total, sell.total) == (Decimal("5.10"), Decimal("10.10"))
    lot_buy = quote_cn_a("B", 1000, raw_price=10, **rates)
    lot_sell = quote_cn_a("S", 1100, raw_price=11, **rates)
    assert lot_buy.total == Decimal("5.01")
    assert lot_sell.total == Decimal("5.56")
    assert lot_buy.total + lot_sell.total == Decimal("10.57")
    assert Decimal("1005") < Decimal("1000") + lot_buy.total
    maker = quote_liquidity("B", 1000, maker_rate="0.0002", taker_rate="0.0004", assumption="ALL_MAKER")
    taker = quote_liquidity("S", 1000, maker_rate="0.0002", taker_rate="0.0004", assumption="ALL_TAKER")
    assert maker.total == Decimal("0.20")
    assert taker.total == Decimal("0.40")
    status, cash, fees = round_trip_cash(10000, 10, 11, 100, _fixture())
    assert status == "FILLED" and fees == Decimal("10.57") and cash == Decimal("10089.43")
    rejected, left, rejected_fees = round_trip_cash(1005, 10, 11, 100, _fixture())
    assert rejected == "REJECTED" and left == Decimal("1005") and rejected_fees == 0
    zero_status, zero_cash, zero_fees = round_trip_cash(1005, 10, 11, 100, None)
    assert zero_status == "FILLED" and zero_fees == 0 and zero_cash == Decimal("1105")
