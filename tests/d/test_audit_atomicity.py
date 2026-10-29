from datetime import date
import uuid

import psycopg
import pytest

from app.db import transaction
from app.trading import create_account
from app.trading.guard import submit_order


def test_repeat_order_key_does_not_create_a_second_order(identities):
    identity, _token = identities[0]
    account = create_account(identity.user_id, "幂等", date(2025, 5, 13), 100000)
    key = "order-" + uuid.uuid4().hex
    first = submit_order(identity, account, "sh.600003", "B", "OPEN", 100, None, key)
    second = submit_order(identity, account, "sh.600003", "B", "OPEN", 100, None, key)
    assert first == second
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM sim.orders WHERE account_id=%s", (account,)).fetchone()[0] == 1
        events = conn.execute(
            "SELECT event_type FROM audit.business_event WHERE object_id=%s",
            (str(first),),
        ).fetchall()
    assert events == [("SIM_ORDER",)]


def test_app_and_agent_cannot_rewrite_request_log(identities):
    identity, _token = identities[0]
    with pytest.raises(psycopg.Error):
        with transaction(user_id=identity.user_id) as conn:
            conn.execute("DELETE FROM audit.request_event")
    with pytest.raises(psycopg.Error):
        with transaction(role="agent", read_only=True) as conn:
            conn.execute("DELETE FROM audit.request_event")
    with pytest.raises(psycopg.Error):
        with transaction(role="auditor") as conn:
            conn.execute("DELETE FROM audit.request_event")
