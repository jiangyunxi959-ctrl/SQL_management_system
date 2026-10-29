from datetime import date
from decimal import Decimal

from app.db import connect_admin
from app.factors.compiler import compile_query
from app.factors.quality import decode_mask
from app.factors.service import list_definitions, read_values, run_factor
from app.factors.validate import validate


def test_suspension_zero_volume_and_corporate_action_keep_reasons(identities):
    identity, _token = identities[0]
    catalog = list_definitions(identity)
    vwap = next(row for row in catalog if row[2] == "VWAP_D")
    vwap20 = next(row for row in catalog if row[2] == "VWAP20_D")
    suspended, _plan = run_factor(identity, vwap[0], 1, "SYNTH300", date(2025, 5, 15))
    reasons = dict((row[0], row[4]) for row in read_values(identity, suspended))
    assert "SUSPENDED" in reasons["sh.600001"]
    assert "ZERO_VOLUME" in reasons["sh.600001"]
    crossed, _plan = run_factor(identity, vwap20[0], 1, "SYNTH300", date(2025, 6, 20))
    later = dict((row[0], row[4]) for row in read_values(identity, crossed))
    assert later["sh.600001"] == ["CORPORATE_ACTION"]
    same_day, _plan = run_factor(identity, vwap[0], 1, "SYNTH300", date(2025, 6, 20))
    plain = next(row for row in read_values(identity, same_day) if row[0] == "sh.600001")
    assert plain[3] == "OK"


def test_missing_bar_is_not_skipped_or_treated_as_weekend():
    expr = validate("ts_mean(close_raw,5)")
    sql = compile_query(expr)
    conn = connect_admin()
    try:
        conn.execute("SELECT set_config('app.as_of_date', '2025-05-30', false)")
        conn.execute(
            "DELETE FROM mkt.daily_bar WHERE stock_code='sh.600003' AND trade_date='2025-05-28'"
        )
        rows = conn.execute(sql, ("SYNTH300", expr.lookback)).fetchall()
        dates = conn.execute(
            """
            SELECT trade_date FROM pit.fn_factor_panel('SYNTH300', 5)
            WHERE stock_code='sh.600003' ORDER BY trade_date
            """
        ).fetchall()
        conn.rollback()
    finally:
        conn.close()
    target = next(row for row in rows if row[0] == "sh.600003")
    codes, _detail = decode_mask(target[3])
    assert target[2] is None
    assert "MISSING_DATA" in codes
    assert date(2025, 5, 24) not in {row[0] for row in dates}
    assert date(2025, 5, 28) in {row[0] for row in dates}


def test_short_history_is_insufficient(identities):
    identity, _token = identities[0]
    mom = next(row for row in list_definitions(identity) if row[2] == "MOM20_D")
    run_id, _plan = run_factor(identity, mom[0], 1, "SYNTH300", date(2024, 1, 8))
    rows = read_values(identity, run_id)
    assert rows
    assert all("INSUFFICIENT_HISTORY" in row[4] for row in rows)
    assert all(row[2] is None for row in rows)
