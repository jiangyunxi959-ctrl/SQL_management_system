from datetime import date

import psycopg
import pytest

from app.agent.gateway import ScriptedModel, ToolRejected, UnconfiguredModel, handle
from app.db import transaction
from app.factors.service import create_private, list_definitions


def test_unconfigured_model_is_not_presented_as_a_real_answer(identities):
    identity, _token = identities[0]
    result = handle(identity, date(2025, 5, 30), "今天买什么", UnconfiguredModel())
    assert result["status"] == "model_not_configured"
    assert result["calls"] == []


def test_scripted_tools_cannot_pass_sql_or_another_user(identities):
    identity, _token = identities[0]
    other, _other = identities[1]
    private_id = create_private(identity, "私有量", "volume")
    with pytest.raises(ToolRejected):
        handle(identity, date(2025, 5, 30), "x", ScriptedModel([
            {"tool": "sql", "args": {"query": "DELETE FROM sim.account"}}
        ]))
    with pytest.raises(ToolRejected):
        handle(identity, date(2025, 5, 30), "x", ScriptedModel([
            {"tool": "list_factors", "args": {"user_id": other.user_id}}
        ]))
    listed = handle(identity, date(2025, 5, 30), "x", ScriptedModel([{"tool": "list_factors", "args": {}}]))
    ids = {item[0] for item in listed["calls"][0]["result"]}
    assert private_id in ids
    hidden = handle(other, date(2025, 5, 30), "x", ScriptedModel([{"tool": "list_factors", "args": {}}]))
    assert private_id not in {item[0] for item in hidden["calls"][0]["result"]}


def test_agent_role_cannot_read_private_tables_or_place_orders(identities):
    with pytest.raises(psycopg.Error):
        with transaction(role="agent", read_only=True, as_of=date(2025, 5, 30)) as conn:
            conn.execute("SELECT * FROM factor.definition")
    with pytest.raises(psycopg.Error):
        with transaction(role="agent", as_of=date(2025, 5, 30)) as conn:
            conn.execute("SELECT sim.fn_place_order(1, 'sh.600003', 'B', 'OPEN', 100, NULL)")
    members = handle(identities[0][0], date(2025, 5, 1), "成员", ScriptedModel([
        {"tool": "index_members", "args": {"universe": "SYNTH300"}}
    ]))
    assert len(members["calls"][0]["result"]) > 0


def test_gateway_run_uses_the_session_date(identities):
    identity, _token = identities[0]
    factor_id = next(row[0] for row in list_definitions(identity) if row[2] == "RANGE_D")
    result = handle(identity, date(2025, 5, 30), "算一下", ScriptedModel([
        {"tool": "run_factor", "args": {"factor_id": factor_id, "version": 1, "universe": "SYNTH300"}}
    ]))
    run_id = result["calls"][0]["result"]
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        assert conn.execute("SELECT as_of FROM factor.run WHERE run_id=%s", (run_id,)).fetchone()[0] == date(2025, 5, 30)
