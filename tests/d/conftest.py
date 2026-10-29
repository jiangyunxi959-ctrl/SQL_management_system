import uuid

import pytest

from app.auth import create_user, login, resolve
from app.db import connect_admin


@pytest.fixture(scope="session", autouse=True)
def d2_installed():
    from app.setup_ai import install
    from app.setup_d3 import install as install_d3
    install()
    install_d3()


@pytest.fixture
def identities():
    password = "test-pass-1"
    created = []
    people = []
    for label in ("甲", "乙"):
        username = f"d{label}_{uuid.uuid4().hex[:8]}"
        create_user(username, label, password)
        token = login(username, password)
        identity = resolve(token)
        created.append(identity.user_id)
        people.append((identity, token))
    yield people
    with connect_admin() as conn:
        with conn.transaction():
            conn.execute("SET LOCAL session_replication_role = replica")
            for user_id in created:
                _delete_owned(conn, user_id)
                conn.execute(
                    "DELETE FROM factor.value WHERE run_id IN (SELECT run_id FROM factor.run WHERE owner_id=%s)",
                    (user_id,),
                )
                conn.execute("DELETE FROM factor.run WHERE owner_id=%s", (user_id,))
                conn.execute(
                    """
                    DELETE FROM factor.version WHERE factor_id IN
                    (SELECT factor_id FROM factor.definition WHERE owner_id=%s)
                    """,
                    (user_id,),
                )
                conn.execute("DELETE FROM factor.definition WHERE owner_id=%s", (user_id,))
                conn.execute("DELETE FROM agent.tool_call WHERE agent_session_id IN (SELECT agent_session_id FROM agent.session WHERE user_id=%s)", (user_id,))
                conn.execute("DELETE FROM agent.session WHERE user_id=%s", (user_id,))
                conn.execute("DELETE FROM audit.idempotency WHERE user_id=%s", (user_id,))
                conn.execute("DELETE FROM audit.business_event WHERE user_id=%s", (user_id,))
                conn.execute("DELETE FROM audit.request_event WHERE user_id=%s", (user_id,))
                conn.execute("DELETE FROM sim.fill WHERE order_id IN (SELECT order_id FROM sim.orders o JOIN sim.account a USING(account_id) WHERE a.owner_id=%s)", (user_id,))
                conn.execute("DELETE FROM sim.cash_ledger WHERE account_id IN (SELECT account_id FROM sim.account WHERE owner_id=%s)", (user_id,))
                conn.execute("DELETE FROM sim.orders WHERE account_id IN (SELECT account_id FROM sim.account WHERE owner_id=%s)", (user_id,))
                conn.execute("DELETE FROM sim.position WHERE account_id IN (SELECT account_id FROM sim.account WHERE owner_id=%s)", (user_id,))
                conn.execute("DELETE FROM sim.account WHERE owner_id=%s", (user_id,))
                conn.execute("DELETE FROM core.auth_session WHERE user_id=%s", (user_id,))
                conn.execute("DELETE FROM core.user_credential WHERE user_id=%s", (user_id,))
                conn.execute("DELETE FROM core.app_user WHERE user_id=%s", (user_id,))
        conn.commit()


def _delete_owned(conn, user_id: int) -> None:
    statements = [
        "DELETE FROM research.factor_trade_cost WHERE trade_id IN (SELECT trade_id FROM research.factor_trade WHERE owner_id=%s)",
        "DELETE FROM research.factor_trade WHERE owner_id=%s",
        "DELETE FROM research.backtest_pnl WHERE owner_id=%s",
        "DELETE FROM research.factor_bt_run WHERE owner_id=%s",
        "DELETE FROM research.factor_eval_group WHERE owner_id=%s",
        "DELETE FROM research.factor_eval_daily WHERE owner_id=%s",
        "DELETE FROM research.factor_eval_value WHERE owner_id=%s",
        "DELETE FROM research.factor_eval_run WHERE owner_id=%s",
        "DELETE FROM research.factor_strategy_version WHERE owner_id=%s",
        "DELETE FROM research.factor_strategy WHERE owner_id=%s",
        "DELETE FROM sim.cost_version WHERE owner_id=%s",
        "DELETE FROM sim.cost_profile WHERE owner_id=%s",
        "DELETE FROM agent.checkpoint_write WHERE owner_id=%s",
        "DELETE FROM agent.graph_checkpoint WHERE owner_id=%s",
        "DELETE FROM agent.tool_execution WHERE owner_id=%s",
        "DELETE FROM agent.evidence WHERE owner_id=%s",
        "DELETE FROM agent.message WHERE owner_id=%s",
        "DELETE FROM agent.turn WHERE owner_id=%s",
        "DELETE FROM agent.conversation WHERE owner_id=%s",
        "DELETE FROM agent.prompt_version WHERE profile_id IN (SELECT profile_id FROM agent.prompt_profile WHERE owner_id=%s)",
        "DELETE FROM agent.prompt_profile WHERE owner_id=%s",
    ]
    for statement in statements:
        table = statement.split()[2]
        if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]:
            conn.execute(statement, (user_id,))
