"""运行真实 Streamlit 页面，验证开户、下单、撮合、回测和图表。"""
from pathlib import Path
from streamlit.testing.v1 import AppTest
from tests.c.test_services import scope

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
