"""三个原策略。这里不把自定义公式当作已经完成的新回测引擎。"""
from datetime import date

import pandas as pd
import psycopg
import streamlit as st

from app.backtest import replay_backtest, run_backtest
from app.db import transaction
from app.ui.session import require_identity
from app.ui.charts import time_series_chart

st.title("原策略")
st.caption("动量、均线和财报这三个模板仍在本页。单因子策略在“因子策略”页。")
identity, _as_of = require_identity()
user = identity.user_id
try:
    with st.form("backtest"):
        template = st.selectbox("策略模板", ["momentum", "ma_cross", "quality"],
                                format_func=lambda item: {"momentum": "20 日动量", "ma_cross": "均线趋势", "quality": "财报 ROE"}[item])
        universe = st.text_input("历史指数股票池", "SYNTH300")
        start = st.date_input("开始日期（日终）", date(2025, 3, 3))
        end = st.date_input("结束日期（日终）", date(2025, 5, 30))
        count = st.number_input("最多持有股票数", min_value=1, max_value=20, value=3, step=1)
        cash = st.number_input("初始资金（元）", min_value=1000, value=100000, step=1000)
        if st.form_submit_button("运行回测"):
            run_id = run_backtest(user, template, {"top_n": count, "initial_cash": cash}, universe, start, end)
            st.session_state["c_last_run"] = run_id
            st.success(f"回测 {run_id} 已完成")
    with transaction(user_id=user, read_only=True) as conn:
        runs = conn.execute(
            "SELECT run_id, start_date, end_date, status, max_drawdown FROM research.backtest_run ORDER BY run_id DESC"
        ).fetchall()
    if not runs:
        st.info("还没有回测记录。")
    else:
        st.dataframe(pd.DataFrame(runs, columns=["回测", "开始日期", "结束日期", "状态", "最大回撤"]), hide_index=True)
        ids = [row[0] for row in runs]
        last = st.session_state.get("c_last_run")
        run_id = st.selectbox("查看回测", ids, index=ids.index(last) if last in ids else 0, key=f"d_pick_backtest_{user}")
        with transaction(user_id=user, read_only=True) as conn:
            nav = conn.execute(
                "SELECT trade_date, nav, drawdown FROM research.backtest_nav WHERE run_id=%s ORDER BY trade_date",
                (run_id,),
            ).fetchall()
        if nav:
            chart = pd.DataFrame(nav, columns=["日期", "净值", "回撤"]).set_index("日期").astype(float)
            st.caption("净值以初始资金为1；当前结果已扣除默认交易费用。纵轴自适应，不强制从0开始。")
            st.altair_chart(time_series_chart(chart.reset_index(),"日期",["净值"],"账户净值（已计默认费用）",baseline=1),width='stretch')
            st.metric("区间收益", f"{(chart['净值'].iloc[-1] - 1) * 100:.2f}%")
            st.metric("最大回撤", f"{-chart['回撤'].min() * 100:.2f}%")
        if st.button("按原参数和财报快照重放"):
            replay = replay_backtest(user, run_id)
            st.session_state["c_last_run"] = replay
            st.success(f"已生成重放回测 {replay}")
except (psycopg.Error, ValueError) as exc:
    st.error(str(exc).split("CONTEXT:")[0])
