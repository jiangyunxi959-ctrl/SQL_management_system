"""在专用样本库上以真实 app_user 连接验收；删除仅限本轮测试的 research/sim 行。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
import uuid

import psycopg
import pytest

from etl.temporal.common import connect
from app.backtest import run_backtest, replay_backtest, select_codes
from app.trading import create_account, place_order, cancel_order, advance_day
from app.trading.db import transaction


@pytest.fixture
def scope():
    with connect() as conn:
        if not conn.info.dbname.endswith('_sample'):
            pytest.fail('C integration tests require an isolated *_sample database')
        users = []
        for _ in range(2):
            u = conn.execute("INSERT INTO core.app_user(username,display_name) VALUES (%s,'C test') RETURNING user_id",
                             ['c_test_'+uuid.uuid4().hex]).fetchone()[0]
            users.append(u)
    yield users
    with connect() as conn:
        # 只追加表不允许 DELETE。测试库使用复制角色跳过触发器，仅清理本次用户的数据。
        conn.execute('SET LOCAL session_replication_role=replica')
        for u in users:
            conn.execute('DELETE FROM research.backtest_trade WHERE run_id IN (SELECT run_id FROM research.backtest_run WHERE owner_id=%s)',[u])
            conn.execute('DELETE FROM research.backtest_nav WHERE run_id IN (SELECT run_id FROM research.backtest_run WHERE owner_id=%s)',[u])
            conn.execute('DELETE FROM research.backtest_run WHERE owner_id=%s',[u])
            conn.execute('DELETE FROM sim.fill WHERE order_id IN (SELECT order_id FROM sim.orders o JOIN sim.account a USING(account_id) WHERE a.owner_id=%s)',[u])
            conn.execute('DELETE FROM sim.cash_ledger WHERE account_id IN(SELECT account_id FROM sim.account WHERE owner_id=%s)',[u])
            conn.execute('DELETE FROM sim.orders WHERE account_id IN(SELECT account_id FROM sim.account WHERE owner_id=%s)',[u])
            conn.execute('DELETE FROM sim.position WHERE account_id IN(SELECT account_id FROM sim.account WHERE owner_id=%s)',[u])
            conn.execute('DELETE FROM sim.account WHERE owner_id=%s',[u])
            conn.execute('DELETE FROM core.app_user WHERE user_id=%s',[u])


def test_app_role_and_t1(scope):
    user, other = scope
    aid = create_account(user,'real app role',date(2025,5,13),100000)
    order = place_order(user,aid,'sh.600003','B','OPEN',100)
    with pytest.raises(psycopg.errors.RaiseException,match=r'T\+1'):
        place_order(user,aid,'sh.600003','S','CLOSE',100)
    assert advance_day(user,aid)[0][1]=='FILLED'
    with transaction(user_id=user,read_only=True) as conn:
        assert conn.execute('SELECT qty,sellable_qty FROM sim.position WHERE account_id=%s',[aid]).fetchone()==(100,0)
        assert conn.execute('SELECT * FROM sim.v_cash_reconcile').fetchall()==[]
    with transaction(user_id=other,read_only=True) as conn:
        assert conn.execute('SELECT * FROM sim.account WHERE account_id=%s',[aid]).fetchall()==[]
        assert conn.execute('SELECT * FROM sim.orders WHERE order_id=%s',[order]).fetchall()==[]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        advance_day(other,aid)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with transaction(user_id=user) as conn:
            conn.execute('UPDATE sim.account SET cash=999999 WHERE account_id=%s',[aid])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with transaction(user_id=user,read_only=True) as conn:
            conn.execute('SELECT * FROM mkt.daily_bar LIMIT 1')
    place_order(user,aid,'sh.600003','S','OPEN',100)
    assert advance_day(user,aid)[0][1]=='FILLED'


def test_50_concurrent_buy_and_sell(scope):
    user = scope[0]
    aid = create_account(user,'50 workers',date(2025,5,13),10000)
    def buy(_):
        try:
            return place_order(user,aid,'sh.600003','B','OPEN',100)
        except psycopg.errors.RaiseException as exc:
            assert '可用资金不足' in str(exc)
            return None
    with ThreadPoolExecutor(max_workers=50) as pool:
        orders=[x for x in pool.map(buy,range(50)) if x is not None]
    assert 0<len(orders)<50
    with transaction(user_id=user,read_only=True) as conn:
        cash,frozen=conn.execute('SELECT cash,frozen_cash FROM sim.account WHERE account_id=%s',[aid]).fetchone()
        assert 0<frozen<=cash
        assert conn.execute('SELECT * FROM sim.v_cash_reconcile').fetchall()==[]
    for oid in orders:
        cancel_order(user,oid)
    place_order(user,aid,'sh.600003','B','OPEN',100)
    advance_day(user,aid)
    def sell(_):
        try:
            return place_order(user,aid,'sh.600003','S','OPEN',100)
        except psycopg.errors.RaiseException as exc:
            assert '可卖持仓不足' in str(exc)
            return None
    with ThreadPoolExecutor(max_workers=50) as pool:
        sells=[x for x in pool.map(sell,range(50)) if x is not None]
    assert len(sells)==1
    advance_day(user,aid)
    with transaction(user_id=user,read_only=True) as conn:
        assert conn.execute('SELECT qty FROM sim.position WHERE account_id=%s',[aid]).fetchone()[0]==0
        assert conn.execute('SELECT * FROM sim.v_cash_reconcile').fetchall()==[]


def test_sql_python_backtests_and_rls(scope):
    user,other=scope
    params={'top_n':3,'initial_cash':100000}
    start,end=date(2025,3,3),date(2025,5,30)
    python_run=run_backtest(user,'momentum',params,'SYNTH300',start,end)
    with transaction(user_id=user) as conn:
        from psycopg.types.json import Jsonb
        sql_run=conn.execute('SELECT research.fn_run_momentum(%s,%s,%s,%s,%s)',[user,Jsonb(params),'SYNTH300',start,end]).fetchone()[0]
    with transaction(user_id=user,read_only=True) as conn:
        navs=[]
        for rid in (python_run,sql_run):
            navs.append(conn.execute('SELECT trade_date,equity,nav,drawdown FROM research.backtest_nav WHERE run_id=%s ORDER BY trade_date',[rid]).fetchall())
        assert len(navs[0])==65 and navs[0]==navs[1]
        assert conn.execute('SELECT count(*) FROM research.backtest_trade WHERE run_id=%s',[python_run]).fetchone()[0]>0
    with transaction(user_id=other,read_only=True) as conn:
        assert conn.execute('SELECT * FROM research.backtest_run WHERE run_id=%s',[python_run]).fetchall()==[]
        assert conn.execute('SELECT * FROM research.backtest_nav WHERE run_id=%s',[python_run]).fetchall()==[]
    for template in ('ma_cross','quality'):
        rid=run_backtest(user,template,params,'SYNTH300',date(2025,5,1),date(2025,5,30))
        with transaction(user_id=user,read_only=True) as conn:
            assert conn.execute('SELECT status FROM research.backtest_run WHERE run_id=%s',[rid]).fetchone()[0]=='COMPLETED'


def test_no_future_financial_signal_and_parameter_errors(scope):
    user=scope[0]
    with transaction(user_id=user,as_of=date(2025,4,25),read_only=True) as conn:
        rows=conn.execute('SELECT roe FROM research.fn_candidates(%s)',['SYNTH300']).fetchall()
        assert rows and all(r[0] is None for r in rows)
    for params in ({'top_n':0},{'unknown':1}):
        with pytest.raises(psycopg.errors.RaiseException):
            run_backtest(user,'momentum',params,'SYNTH300',date(2025,5,1),date(2025,5,30))
    with pytest.raises(ValueError):
        select_codes([], 'unknown',5)


def test_financial_snapshot_replays_quality(scope):
    user=scope[0]
    with connect() as conn:
        snapshot=conn.execute('SELECT clock_timestamp()').fetchone()[0]
    params={'top_n':1,'initial_cash':100000,'data_snapshot_at':snapshot.isoformat()}
    original=run_backtest(user,'quality',params,'SYNTH300',date(2025,5,1),date(2025,5,14))
    with connect() as conn:
        old=conn.execute("SELECT roe FROM fin.report WHERE stock_code='sh.600001' AND announce_date='2025-04-28'").fetchone()[0]
        conn.execute("UPDATE fin.report SET roe=0.01 WHERE stock_code='sh.600001' AND announce_date='2025-04-28'")
    try:
        replay=replay_backtest(user,original)
        current=run_backtest(user,'quality',{'top_n':1},'SYNTH300',date(2025,5,1),date(2025,5,14))
        with transaction(user_id=user,read_only=True) as conn:
            def nav(rid):
                return conn.execute('SELECT trade_date,nav FROM research.backtest_nav WHERE run_id=%s ORDER BY trade_date',[rid]).fetchall()
            assert nav(original)==nav(replay)
            assert nav(original)!=nav(current)
        with pytest.raises(ValueError,match='无权访问'):
            replay_backtest(scope[1],original)
        with connect() as conn:
            conn.execute("UPDATE mkt.daily_bar SET amount=amount+1 WHERE stock_code='sh.600003' AND trade_date='2025-05-13'")
        try:
            with pytest.raises(psycopg.errors.RaiseException,match='行情或规则已变化'):
                replay_backtest(user,original)
        finally:
            with connect() as conn:
                conn.execute("UPDATE mkt.daily_bar SET amount=amount-1 WHERE stock_code='sh.600003' AND trade_date='2025-05-13'")
    finally:
        with connect() as conn:
            conn.execute("UPDATE fin.report SET roe=%s WHERE stock_code='sh.600001' AND announce_date='2025-04-28'",[old])
