"""独立引擎对照：同一原始行情、次日开盘、100 股买入持有、相同费用。"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import backtrader as bt
import pandas as pd

from etl.temporal.common import connect
from app.trading import create_account,place_order,advance_day
from app.trading.db import transaction
from tests.c.test_services import scope


class ChinaBuyFee(bt.CommInfoBase):
    params=(('stocklike',True),('commtype',bt.CommInfoBase.COMM_FIXED))
    def _getcommission(self,size,price,pseudoexec):
        amount=Decimal(abs(size))*Decimal(str(price))
        cents=Decimal('.01')
        commission=max(amount*Decimal('.0003'),Decimal('5')).quantize(cents,ROUND_HALF_UP)
        transfer=(amount*Decimal('.00001')).quantize(cents,ROUND_HALF_UP)
        stamp=(amount*Decimal('.0005')).quantize(cents,ROUND_HALF_UP) if size<0 else Decimal(0)
        return float(commission+transfer+stamp)


class BuyOnce(bt.Strategy):
    def __init__(self):
        self.sent=False
        self.executed=[]
    def next(self):
        if not self.sent:
            self.buy(size=100)
            self.sent=True
    def notify_order(self,order):
        if order.status==order.Completed:
            self.executed.append((self.data.datetime.date(),order.executed.price,order.executed.comm))


def test_buy_hold_matches_backtrader(scope):
    user=scope[0]
    with connect() as conn:
        rows=conn.execute("SELECT trade_date,round(open,2),round(high,2),round(low,2),close,volume FROM mkt.daily_bar WHERE stock_code='sh.600003' AND trade_date BETWEEN '2025-05-13' AND '2025-05-23' ORDER BY trade_date").fetchall()
    df=pd.DataFrame(rows,columns=['datetime','open','high','low','close','volume'])
    df['datetime']=pd.to_datetime(df['datetime'])
    df=df.set_index('datetime').astype(float)
    cerebro=bt.Cerebro()
    cerebro.broker.setcash(100000)
    cerebro.broker.addcommissioninfo(ChinaBuyFee())
    cerebro.adddata(bt.feeds.PandasData(dataname=df))
    cerebro.addstrategy(BuyOnce)
    strategy=cerebro.run()[0]
    assert len(strategy.executed)==1
    aid=create_account(user,'backtrader comparison',date(2025,5,13),100000)
    oid=place_order(user,aid,'sh.600003','B','OPEN',100)
    for _ in rows[1:]:
        advance_day(user,aid)
    with transaction(user_id=user,read_only=True) as conn:
        cash=conn.execute('SELECT cash FROM sim.account WHERE account_id=%s',[aid]).fetchone()[0]
        fill=conn.execute('SELECT trade_date,price,fee FROM sim.fill WHERE order_id=%s',[oid]).fetchone()
    assert fill[0]==strategy.executed[0][0]
    assert abs(float(fill[1])-strategy.executed[0][1])<1e-9
    assert abs(float(fill[2])-strategy.executed[0][2])<1e-9
    equity=float(cash)+100*float(rows[-1][4])
    assert abs(equity-cerebro.broker.getvalue())<1e-6
