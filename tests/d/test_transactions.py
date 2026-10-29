from datetime import date

import pytest

from app.db import _connect, transaction


def test_same_connection_clears_user_and_date_after_error():
    conn = _connect("app")
    try:
        with pytest.raises(RuntimeError):
            with transaction(_connection=conn, user_id=11, as_of=date(2025, 5, 1)) as live:
                assert live.execute("SELECT current_setting('app.user_id', true)").fetchone()[0] == "11"
                raise RuntimeError("boom")
        with transaction(_connection=conn, user_id=22, as_of=date(2025, 6, 2), read_only=True) as live:
            assert live.execute("SELECT current_setting('app.user_id', true)").fetchone()[0] == "22"
            assert live.execute("SELECT current_setting('app.as_of_date', true)").fetchone()[0] == "2025-06-02"
            assert live.execute("SELECT current_setting('app.data_snapshot_at', true)").fetchone()[0] == ""
    finally:
        conn.close()


def test_role_name_is_not_accepted_as_a_database_role():
    with pytest.raises(ValueError, match="未知"):
        with transaction(role="postgres"):
            pass
