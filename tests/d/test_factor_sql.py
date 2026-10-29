from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.db import transaction
from app.factors.execution import _PANEL
from app.factors.reference import evaluate
from app.factors.service import list_definitions, read_values, run_factor
from app.factors.validate import validate

TOLERANCE = Decimal("0.000001")


def test_database_results_match_independent_reference(identities):
    identity, _token = identities[0]
    catalog = list_definitions(identity)
    as_of = date(2025, 6, 20)
    for code in sorted({row[2] for row in catalog if row[1] == "SYSTEM_PUBLIC"}):
        item = next(row for row in catalog if row[2] == code and row[5] == 1)
        expr = validate(item[6])
        with transaction(user_id=identity.user_id, as_of=as_of, read_only=True) as conn:
            panel = conn.execute(_PANEL, ("SYNTH300", expr.lookback)).fetchall()
        grouped = defaultdict(list)
        for row in panel:
            grouped[row[0]].append(_row(row))
        run_id, plan = run_factor(identity, item[0], 1, "SYNTH300", as_of)
        if expr.lookback > 1:
            assert "WindowAgg" in plan
        for stock, _trade, value, quality, _reasons, _detail in read_values(identity, run_id):
            expected, _mask = evaluate(expr, grouped[stock])
            if quality == "OK":
                assert expected is not None
                assert abs(Decimal(value) - expected) <= TOLERANCE
            else:
                assert expected is None


def test_saved_results_agree_with_b_when_both_are_valid(identities):
    identity, _token = identities[0]
    catalog = list_definitions(identity)
    as_of = date(2025, 6, 20)
    wanted = {"MOM20_D": "ret_20d", "VOL20_D": "vol_20d", "MA20_BIAS_D": "bias"}
    for code, column in wanted.items():
        item = next(row for row in catalog if row[2] == code and row[5] == 1)
        run_id, _plan = run_factor(identity, item[0], 1, "SYNTH300", as_of)
        with transaction(user_id=identity.user_id, as_of=as_of, read_only=True) as conn:
            compared = conn.execute(
                """
                SELECT max(abs(v.value - CASE %s
                    WHEN 'ret_20d' THEN i.ret_20d
                    WHEN 'vol_20d' THEN i.vol_20d
                    ELSE i.close_hfq / i.ma20 - 1 END))
                FROM factor.value v
                JOIN pit.indicator_daily i ON i.stock_code=v.stock_code AND i.trade_date=v.trade_date
                WHERE v.run_id=%s AND v.quality='OK' AND i.ma20 IS NOT NULL
                """,
                (column, run_id),
            ).fetchone()[0]
        assert compared is not None
        assert compared <= TOLERANCE


def _row(row):
    names = (
        "trade_date", "day_ord", "open_raw", "high_raw", "low_raw", "close_raw",
        "open_hfq", "high_hfq", "low_hfq", "close_hfq", "volume", "amount",
        "is_suspended", "has_bar", "back_factor", "ca_in_window",
    )
    return dict(zip(names, row[1:]))
