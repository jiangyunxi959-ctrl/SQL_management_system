import psycopg
import pytest

from app.db import connect_admin, transaction
from app.factors.errors import FactorError
from app.factors.service import create_private, run_factor
from datetime import date


def test_rejected_formula_is_recorded_without_a_version(identities):
    identity, _token = identities[0]
    with pytest.raises(FactorError):
        create_private(identity, "坏公式", "close_raw; DELETE FROM sim.account")
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM factor.definition WHERE name='坏公式'").fetchone()[0] == 0
        kinds = [row[0] for row in conn.execute(
            "SELECT event_type FROM audit.request_event WHERE tool_name='factor.create' ORDER BY event_seq"
        )]
    assert "START" in kinds and "REJECTED" in kinds


def test_failed_run_does_not_leave_a_success_row(identities):
    identity, _token = identities[0]
    from app.factors.service import list_definitions
    public_id = next(row[0] for row in list_definitions(identity) if row[2] == "VWAP_D")
    before = _runs(identity.user_id)
    with pytest.raises(psycopg.Error):
        run_factor(identity, public_id, 1, "NO_SUCH", date(2025, 5, 30))
    assert _runs(identity.user_id) == before
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        kinds = [row[0] for row in conn.execute(
            "SELECT event_type FROM audit.request_event WHERE tool_name='factor.run' ORDER BY created_at, event_seq"
        )]
    assert "FAILURE" in kinds


def test_business_rollback_keeps_request_failure(identities):
    identity, _token = identities[0]
    with pytest.raises(RuntimeError):
        with transaction(user_id=identity.user_id) as conn:
            conn.execute("SELECT set_config('app.request_id', '00000000-0000-0000-0000-000000000099', true)")
            conn.execute(
                "SELECT sim.fn_create_account(%s::bigint, %s::text, %s::date, %s::numeric)",
                (identity.user_id, "不会留下", date(2025, 5, 13), 100000),
            )
            raise RuntimeError("forced")
    with connect_admin() as conn:
        accounts = conn.execute(
            "SELECT count(*) FROM sim.account WHERE owner_id=%s AND name='不会留下'",
            (identity.user_id,),
        ).fetchone()[0]
    assert accounts == 0


def _runs(user_id):
    with connect_admin() as conn:
        return conn.execute("SELECT count(*) FROM factor.run WHERE owner_id=%s", (user_id,)).fetchone()[0]
