"""运行真实 Streamlit 页面，验证开户、下单、撮合、回测和图表。"""
from pathlib import Path
from streamlit.testing.v1 import AppTest
from tests.c.test_services import scope
from datetime import date
from app.trading import create_account
from app.trading.db import transaction

ROOT=Path(__file__).resolve().parents[2]


def test_trading_page(scope):
    app=AppTest.from_file(str(ROOT/'app/ui/pages/c_trading.py')).run(timeout=30)
    app.number_input[0].set_value(scope[0]).run()
    assert not app.exception and not app.error
    next(b for b in app.button if b.label=='创建账户').click().run()
    assert not app.exception and not app.error
    next(b for b in app.button if b.label=='提交订单').click().run()
    assert not app.exception and not app.error
    next(b for b in app.button if b.label=='推进一个交易日并撮合').click().run()
    assert not app.exception and not app.error
    assert any('对账通过' in x.value for x in app.success)


def test_backtest_page(scope):
    app=AppTest.from_file(str(ROOT/'app/ui/pages/c_backtest.py')).run(timeout=30)
    app.number_input[0].set_value(scope[0]).run()
    assert not app.exception and not app.error
    next(b for b in app.button if b.label=='运行回测').click().run(timeout=60)
    assert not app.exception and not app.error
    assert len(app.metric)==2
    next(b for b in app.button if b.label=='按原参数和财报快照重放').click().run(timeout=60)
    assert not app.exception and not app.error


def test_trading_selection_survives_account_list_changes(scope):
    user, other = scope
    create_account(user, 'first', date(2025, 5, 13), 100000)
    selected = create_account(user, 'selected', date(2025, 5, 13), 100000)
    other_account = create_account(other, 'other', date(2025, 5, 13), 100000)
    app = AppTest.from_file(str(ROOT/'app/ui/pages/c_trading.py')).run(timeout=30)
    app.number_input[0].set_value(user).run()
    next(s for s in app.selectbox if s.label == '操作账户').select(selected).run()
    create_account(user, 'created in another page', date(2025, 5, 13), 100000)
    app.run()
    assert next(s for s in app.selectbox if s.label == '操作账户').value == selected
    next(b for b in app.button if b.label == '提交订单').click().run()
    assert not app.exception and not app.error
    with transaction(user_id=user, read_only=True) as conn:
        assert conn.execute('SELECT account_id FROM sim.orders').fetchall() == [(selected,)]
    next(b for b in app.button if b.label == '创建账户').click().run()
    assert not app.exception and not app.error
    with transaction(user_id=user, read_only=True) as conn:
        created = conn.execute('SELECT max(account_id) FROM sim.account').fetchone()[0]
    assert next(s for s in app.selectbox if s.label == '操作账户').value == created
    app.number_input[0].set_value(other).run()
    assert next(s for s in app.selectbox if s.label == '操作账户').value == other_account
