"""生成可查看的 SQL/Python 动量对照与三个模板示例，不修改 A/B 数据。"""
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
from psycopg.types.json import Jsonb
from app.backtest import run_backtest
from app.trading.db import transaction
from etl.temporal.common import connect

OUT=Path(__file__).resolve().parent


if __name__=='__main__':
    with connect() as conn:
        if not conn.info.dbname.endswith('_sample'):
            raise SystemExit('Only run in an isolated *_sample database')
        user=conn.execute("SELECT user_id FROM core.app_user WHERE username='demo_user'").fetchone()[0]
    params={'top_n':3,'initial_cash':100000}
    start,end=date(2025,3,3),date(2025,5,30)
    runs={}
    runs['python_momentum']=run_backtest(user,'momentum',params,'SYNTH300',start,end)
    with transaction(user_id=user) as conn:
        runs['sql_momentum']=conn.execute('SELECT research.fn_run_momentum(%s,%s,%s,%s,%s)',[user,Jsonb(params),'SYNTH300',start,end]).fetchone()[0]
    for template in ('ma_cross','quality'):
        runs[template]=run_backtest(user,template,params,'SYNTH300',start,end)
    curves={}
    summary=[]
    with transaction(user_id=user,read_only=True) as conn:
        for label,rid in runs.items():
            rows=conn.execute('SELECT trade_date,equity,nav,drawdown FROM research.backtest_nav WHERE run_id=%s ORDER BY trade_date',[rid]).fetchall()
            frame=pd.DataFrame(rows,columns=['trade_date','equity','nav','drawdown'])
            frame.to_csv(OUT/(label+'.csv'),index=False)
            curves[label]=frame.set_index('trade_date')['nav'].astype(float)
            fills=conn.execute('SELECT count(*) FROM research.backtest_trade t JOIN sim.fill f USING(order_id) WHERE t.run_id=%s',[rid]).fetchone()[0]
            summary.append(dict(label=label,run_id=rid,days=len(frame),filled_orders=fills,
                                total_return=float(frame['nav'].iloc[-1])-1,max_drawdown=-float(frame['drawdown'].min())))
    assert curves['python_momentum'].equals(curves['sql_momentum'])
    chart=pd.DataFrame(curves)
    chart.index=pd.to_datetime(chart.index)
    chart.resample('ME').last().pct_change().to_csv(OUT/'monthly_returns.csv')
    # 第一个月也以最初本金为基数，不能丢掉首月收益。
    monthly=chart.resample('ME').last()
    returns=monthly.pct_change()
    returns.iloc[0]=monthly.iloc[0]-1
    returns.to_csv(OUT/'monthly_returns.csv')
    pd.DataFrame(summary).to_csv(OUT/'summary.csv',index=False)
    fig,ax=plt.subplots(figsize=(9,4.5))
    chart.plot(ax=ax)
    ax.set(xlabel='Date',ylabel='NAV',title='C sample backtests (synthetic prices, after fees)')
    ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(OUT/'nav.png',dpi=160)
    plt.close(fig)
    print(pd.DataFrame(summary).to_string(index=False))
    print('[PASS] SQL/Python daily NAV and monthly returns are identical; demo runs retained for UI')
