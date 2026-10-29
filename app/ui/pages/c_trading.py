"""C 独立模拟交易页，D 可直接放入导航。用户身份最终由 D 登录提供。"""
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import pandas as pd
import psycopg
import streamlit as st
from app.trading import create_account,place_order,cancel_order,advance_day
from app.trading.db import transaction

st.set_page_config(page_title='知时 · 模拟交易',layout='wide')
st.title('模拟交易')
st.caption('独立演示页，需要手填用户编号。正式使用请从统一入口登录。日终提交，下一交易日开盘或收盘撮合。')
user=int(st.number_input('演示用户 ID',min_value=1,value=2,step=1))
account_key=f'c_trading_account_{user}'
try:
    with st.form('create_account'):
        name=st.text_input('账户名称','我的模拟账户')
        start=st.date_input('起始日（日终）',date(2025,5,13))
        cash=st.number_input('初始资金（元）',min_value=1000,value=100000,step=1000)
        if st.form_submit_button('创建账户'):
            aid=create_account(user,name,start,cash)
            st.session_state[account_key]=aid
            st.success(f'已创建账户 {aid}')
    with transaction(user_id=user,read_only=True) as conn:
        accounts=conn.execute('SELECT account_id,name,as_of_date,cash,frozen_cash FROM sim.account ORDER BY account_id').fetchall()
    if not accounts:
        st.info('先创建一个账户，再提交订单。')
    else:
        frame=pd.DataFrame(accounts,columns=['账户ID','名称','当前日终','资金余额','冻结资金'])
        st.dataframe(frame,hide_index=True)
        account_ids=[r[0] for r in accounts]
        selected=st.session_state.get(account_key)
        index=account_ids.index(selected) if selected in account_ids else 0
        aid=st.selectbox('操作账户',account_ids,index=index,key=account_key)
        with st.form('place_order'):
            code=st.text_input('股票代码','sh.600003')
            side=st.selectbox('方向',['B','S'],format_func=lambda x:'买入' if x=='B' else '卖出')
            point=st.selectbox('执行点',['OPEN','CLOSE'],format_func=lambda x:'下一交易日开盘' if x=='OPEN' else '下一交易日收盘')
            qty=st.number_input('数量（股，100 的整数倍）',min_value=100,value=100,step=100)
            price=st.number_input('限价（元，0 表示按执行点价格）',min_value=0.0,value=0.0,step=.01,format='%.2f')
            if st.form_submit_button('提交订单'):
                oid=place_order(user,aid,code,side,point,int(qty),Decimal(str(price)) if price else None)
                st.success(f'订单 {oid} 已提交')
        if st.button('推进一个交易日并撮合'):
            results=advance_day(user,aid)
            if results:
                st.dataframe(pd.DataFrame(results,columns=['订单ID','状态','说明']),hide_index=True)
            st.success('已推进到下一交易日日终')
        with transaction(user_id=user,read_only=True) as conn:
            orders=conn.execute('SELECT order_id,stock_code,side,exec_date,qty,status,reason FROM sim.orders WHERE account_id=%s ORDER BY order_id',[aid]).fetchall()
            positions=conn.execute('SELECT stock_code,qty,sellable_qty,frozen_qty FROM sim.position WHERE account_id=%s',[aid]).fetchall()
            ledger=conn.execute('SELECT event_date,kind,delta,balance_after FROM sim.cash_ledger WHERE account_id=%s ORDER BY ledger_id',[aid]).fetchall()
            errors=conn.execute('SELECT * FROM sim.v_cash_reconcile WHERE account_id=%s',[aid]).fetchall()
        pending=[r[0] for r in orders if r[5]=='PENDING']
        if pending:
            oid=st.selectbox('待撤订单',pending)
            if st.button('撤销订单'):
                cancel_order(user,oid)
                st.rerun()
        st.subheader('订单')
        st.dataframe(pd.DataFrame(orders,columns=['订单ID','代码','方向','执行日','数量','状态','说明']),hide_index=True)
        st.subheader('持仓')
        st.dataframe(pd.DataFrame(positions,columns=['代码','持有数量','当前日已解锁数量','次日订单冻结数量']),hide_index=True)
        st.subheader('资金流水')
        st.dataframe(pd.DataFrame(ledger,columns=['日期','类型','变动金额','余额']),hide_index=True)
        if errors:
            st.error('对账异常，请停止交易并检查账本。')
        else:
            st.success('资金与冻结金额对账通过')
except (psycopg.Error,ValueError,KeyError) as exc:
    st.error(str(exc).split('CONTEXT:')[0])
