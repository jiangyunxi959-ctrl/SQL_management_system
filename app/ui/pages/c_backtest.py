"""C 独立回测页，D 可直接接入统一导航。"""
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import pandas as pd
import psycopg
import streamlit as st
from app.backtest import run_backtest,replay_backtest
from app.trading.db import transaction
from app.ui.charts import time_series_chart

st.set_page_config(page_title='知时 · 策略回测',layout='wide')
st.title('策略回测')
st.caption('这是旧的独立回测页。正式入口是统一页面 http://127.0.0.1:8510 ，不要在这里手填用户编号。')
user=int(st.number_input('演示用户 ID',min_value=1,value=2,step=1))
try:
    with st.form('backtest'):
        template=st.selectbox('策略模板',['momentum','ma_cross','quality'],format_func=lambda x:{'momentum':'20 日动量','ma_cross':'均线趋势','quality':'财报 ROE'}[x])
        universe=st.text_input('历史指数股票池','SYNTH300')
        start=st.date_input('开始日期（日终）',date(2025,3,3))
        end=st.date_input('结束日期（日终）',date(2025,5,30))
        n=st.number_input('最多持有股票数',min_value=1,max_value=20,value=3,step=1)
        cash=st.number_input('初始资金（元）',min_value=1000,value=100000,step=1000)
        if st.form_submit_button('运行回测'):
            with st.spinner('正在计算……'):
                rid=run_backtest(user,template,{'top_n':n,'initial_cash':cash},universe,start,end)
            st.session_state['c_last_run']=rid
            st.success(f'回测 {rid} 已完成')
    with transaction(user_id=user,read_only=True) as conn:
        runs=conn.execute('SELECT run_id,start_date,end_date,status,max_drawdown FROM research.backtest_run ORDER BY run_id DESC').fetchall()
    if not runs:
        st.info('还没有回测记录，选择策略和日期后运行。')
    else:
        st.dataframe(pd.DataFrame(runs,columns=['回测ID','开始日期','结束日期','状态','最大回撤']),hide_index=True)
        ids=[r[0] for r in runs]
        last=st.session_state.get('c_last_run')
        rid=st.selectbox('查看回测',ids,index=ids.index(last) if last in ids else 0)
        with transaction(user_id=user,read_only=True) as conn:
            nav=conn.execute('SELECT trade_date,nav,drawdown FROM research.backtest_nav WHERE run_id=%s ORDER BY trade_date',[rid]).fetchall()
            trades=conn.execute('SELECT o.exec_date,o.stock_code,o.side,o.qty,o.status,o.reason,f.price,f.fee FROM research.backtest_trade t JOIN sim.orders o USING(order_id) LEFT JOIN sim.fill f USING(order_id) WHERE t.run_id=%s ORDER BY o.order_id',[rid]).fetchall()
        if nav:
            chart=pd.DataFrame(nav,columns=['日期','净值','回撤']).set_index('日期').astype(float)
            st.caption('净值已扣除默认交易费用，纵轴按区间自动缩放。')
            st.altair_chart(time_series_chart(chart.reset_index(),'日期',['净值'],'账户净值（已计默认费用）',baseline=1),width='stretch')
            st.metric('区间收益',f'{(chart["净值"].iloc[-1]-1)*100:.2f}%')
            st.metric('最大回撤',f'{-chart["回撤"].min()*100:.2f}%')
        st.dataframe(pd.DataFrame(trades,columns=['执行日','代码','方向','数量','状态','说明','成交价','费用']),hide_index=True)
        if st.button('按原参数和财报快照重放'):
            new_id=replay_backtest(user,rid)
            st.session_state['c_last_run']=new_id
            st.success(f'已生成重放回测 {new_id}')
except (psycopg.Error,ValueError,KeyError) as exc:
    st.error(str(exc).split('CONTEXT:')[0])
