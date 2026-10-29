"""正式模拟交易。身份来自登录，账户选择不会因为列表变化而跳到别的账户。"""
from datetime import date
from decimal import Decimal
import uuid

import pandas as pd
import psycopg
import streamlit as st

from app.db import transaction
from app.trading import advance_day, cancel_order, create_account
from app.trading.guard import submit_order
from app.ui.session import require_identity

st.title("模拟交易")
st.caption("订单由你明确提交，并继续使用原来的资金、持仓和交易日检查。")
identity, _as_of = require_identity()
user = identity.user_id
account_key = f"d_account_{user}"
try:
    with st.form("create_account"):
        name = st.text_input("账户名称", "我的模拟账户")
        start = st.date_input("起始日（日终）", date(2025, 5, 13))
        cash = st.number_input("初始资金（元）", min_value=1000, value=100000, step=1000)
        if st.form_submit_button("创建账户"):
            created = create_account(user, name, start, cash)
            st.session_state[account_key] = created
            st.success(f"已创建账户 {created}")
    with transaction(user_id=user, read_only=True) as conn:
        accounts = conn.execute(
            "SELECT account_id, name, as_of_date, cash, frozen_cash FROM sim.account ORDER BY account_id"
        ).fetchall()
    if not accounts:
        st.info("先创建一个账户，再提交订单。")
    else:
        st.dataframe(pd.DataFrame(accounts, columns=["账户", "名称", "当前日终", "资金余额", "冻结资金"]), hide_index=True)
        account_ids = [row[0] for row in accounts]
        selected = st.session_state.get(account_key)
        index = account_ids.index(selected) if selected in account_ids else 0
        aid = st.selectbox("操作账户", account_ids, index=index, key=account_key)
        prefill = st.session_state.get(f"d_prefill_{user}") or "sh.600003"
        with st.form("place_order"):
            code = st.text_input("股票代码", prefill)
            side = st.selectbox("方向", ["B", "S"], format_func=lambda item: "买入" if item == "B" else "卖出")
            point = st.selectbox("执行点", ["OPEN", "CLOSE"], format_func=lambda item: "下一交易日开盘" if item == "OPEN" else "下一交易日收盘")
            qty = st.number_input("数量（股，100 的整数倍）", min_value=100, value=100, step=100)
            price = st.number_input("限价（元，0 表示按执行点价格）", min_value=0.0, value=0.0, step=0.01, format="%.2f")
            if st.form_submit_button("提交模拟订单"):
                order_id = submit_order(
                    identity, aid, code, side, point, int(qty),
                    Decimal(str(price)) if price else None, "ui-" + uuid.uuid4().hex,
                )
                st.success(f"订单 {order_id} 已提交")
        if st.button("推进一个交易日并撮合"):
            results = advance_day(user, aid)
            if results:
                st.dataframe(pd.DataFrame(results, columns=["订单", "状态", "说明"]), hide_index=True)
        with transaction(user_id=user, read_only=True) as conn:
            orders = conn.execute(
                "SELECT order_id, stock_code, side, exec_date, qty, status, reason FROM sim.orders WHERE account_id=%s ORDER BY order_id",
                (aid,),
            ).fetchall()
            positions = conn.execute(
                "SELECT stock_code, qty, sellable_qty, frozen_qty FROM sim.position WHERE account_id=%s", (aid,)
            ).fetchall()
        pending = [row[0] for row in orders if row[5] == "PENDING"]
        if pending:
            order_id = st.selectbox("待撤订单", pending, key=f"d_pick_cancel_{user}")
            if st.button("撤销订单"):
                cancel_order(user, order_id)
                st.rerun()
        st.subheader("订单")
        st.dataframe(pd.DataFrame(orders, columns=["订单", "代码", "方向", "执行日", "数量", "状态", "说明"]), hide_index=True)
        st.subheader("持仓")
        st.dataframe(pd.DataFrame(positions, columns=["代码", "持有数量", "当前日已解锁数量", "次日订单冻结数量"]), hide_index=True)
except (psycopg.Error, ValueError) as exc:
    st.error(str(exc).split("CONTEXT:")[0])
