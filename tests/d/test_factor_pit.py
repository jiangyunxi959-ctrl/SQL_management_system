from datetime import date
from decimal import Decimal

from app.db import connect_admin, transaction
from app.factors.service import list_definitions, read_values, run_factor


def test_future_prices_do_not_change_past_results(identities):
    identity, _token = identities[0]
    item = next(row for row in list_definitions(identity) if row[2] == "MOM20_D")
    as_of = date(2025, 5, 30)
    first, _plan = run_factor(identity, item[0], 1, "SYNTH300", as_of)
    first_values = read_values(identity, first)
    digest = _digest(first)
    with connect_admin() as conn:
        original = conn.execute(
            "SELECT amount FROM mkt.daily_bar WHERE stock_code='sh.600003' AND trade_date='2025-06-03'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE mkt.daily_bar SET amount=amount+1 WHERE stock_code='sh.600003' AND trade_date='2025-06-03'"
        )
        conn.commit()
    try:
        second, _plan = run_factor(identity, item[0], 1, "SYNTH300", as_of)
        assert _digest(second) == digest
        assert read_values(identity, first) == first_values
    finally:
        with connect_admin() as conn:
            conn.execute(
                "UPDATE mkt.daily_bar SET amount=%s WHERE stock_code='sh.600003' AND trade_date='2025-06-03'",
                (original,),
            )
            conn.commit()


def test_changed_past_input_changes_only_the_new_digest(identities):
    identity, _token = identities[0]
    item = next(row for row in list_definitions(identity) if row[2] == "VWAP_D")
    as_of = date(2025, 5, 30)
    first, _plan = run_factor(identity, item[0], 1, "SYNTH300", as_of)
    before = read_values(identity, first)
    old_digest = _digest(first)
    with connect_admin() as conn:
        original = conn.execute(
            "SELECT amount FROM mkt.daily_bar WHERE stock_code='sh.600003' AND trade_date='2025-05-30'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE mkt.daily_bar SET amount=amount+100 WHERE stock_code='sh.600003' AND trade_date='2025-05-30'"
        )
        conn.commit()
    try:
        second, _plan = run_factor(identity, item[0], 1, "SYNTH300", as_of)
        assert _digest(second) != old_digest
        assert read_values(identity, first) == before
        changed = next(row for row in read_values(identity, second) if row[0] == "sh.600003")
        original_value = next(row for row in before if row[0] == "sh.600003")
        assert Decimal(changed[2]) != Decimal(original_value[2])
    finally:
        with connect_admin() as conn:
            conn.execute(
                "UPDATE mkt.daily_bar SET amount=%s WHERE stock_code='sh.600003' AND trade_date='2025-05-30'",
                (original,),
            )
            conn.commit()


def _digest(run_id):
    with connect_admin() as conn:
        return conn.execute("SELECT input_digest FROM factor.run WHERE run_id=%s", (run_id,)).fetchone()[0]
