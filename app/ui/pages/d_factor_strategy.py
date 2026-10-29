"""单因子策略。页面展示参数，确认后才回测。"""
from datetime import timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from app.backtest.factor import create_strategy, run_factor_backtest, wait_backtest
from app.costs.service import list_costs
from app.db import transaction
from app.factors.service import list_definitions
from app.ui.charts import time_series_chart
from app.ui.session import require_identity

st.title("因子策略")
st.caption("t 日收盘只决定目标，下一交易日开盘才成交。原策略页仍然保留。")
identity, as_of = require_identity()
rows = [row for row in list_definitions(identity) if not row[4] and row[1] in {"USER_PRIVATE", "SYSTEM_PUBLIC"}]
costs = list_costs(identity)
if not rows:
    st.info("先保存私有公式。")
    st.stop()
labels = {}
ids = []
for row in rows:
    if row[0] not in labels:
        ids.append(row[0])
        labels[row[0]] = row[3]
factor_id = st.selectbox("公式", ids, format_func=lambda item: labels[item], key=f"d_strategy_factor_{identity.user_id}")
versions = [row[5] for row in rows if row[0] == factor_id]
version = st.selectbox("公式版本", versions, index=len(versions) - 1, key=f"d_strategy_version_{identity.user_id}")
name = st.text_input("策略名称", "单因子试运行", key=f"d_strategy_name_{identity.user_id}")
direction = st.selectbox("偏好", ["HIGH", "LOW"], format_func=lambda item: "高分" if item == "HIGH" else "低分", key=f"d_strategy_direction_{identity.user_id}")
sizing = st.selectbox("选股方式", ["只数", "前百分比"], key=f"d_strategy_sizing_{identity.user_id}")
top_n = st.number_input("最多持有只数", min_value=1, max_value=20, value=1, key=f"d_strategy_top_{identity.user_id}")
top_pct = st.number_input("前百分比（0到1）", min_value=0.05, max_value=1.0, value=0.2, key=f"d_strategy_pct_{identity.user_id}")
rebalance = st.selectbox("调仓", ["DAY", "WEEK", "MONTH"], format_func=lambda item: {"DAY": "每个交易日", "WEEK": "每周最后交易日", "MONTH": "每月最后交易日"}[item], key=f"d_strategy_rebalance_{identity.user_id}")
if st.button("保存策略版本"):
    try:
        if sizing == "只数":
            strategy_id, strategy_version = create_strategy(identity, name, factor_id, int(version), direction, int(top_n), rebalance)
        else:
            strategy_id, strategy_version = create_strategy(identity, name, factor_id, int(version), direction, None, rebalance, top_pct=top_pct)
        st.session_state[f"d_strategy_saved_{identity.user_id}"] = (strategy_id, strategy_version)
        st.success(f"策略 {strategy_id} 第 {strategy_version} 版已保存，尚未回测。")
    except Exception as exc:
        st.error(str(exc).split("CONTEXT:")[0])
saved = st.session_state.get(f"d_strategy_saved_{identity.user_id}")
if not costs:
    st.info("回测前先在个人成本设置里保存一版费用。")
elif saved:
    options = [f"{row[0]}:{row[2]}" for row in costs]
    chosen = st.selectbox("费用版本", options, key=f"d_strategy_cost_{identity.user_id}")
    start = st.date_input("回测开始", value=as_of - timedelta(days=15), key=f"d_strategy_start_{identity.user_id}")
    end = st.date_input("回测结束", value=as_of, key=f"d_strategy_end_{identity.user_id}")
    cash = st.number_input("初始资金", value=100000.0, min_value=100.0, key=f"d_strategy_cash_{identity.user_id}")
    if end > as_of or start > as_of:
        st.error("回测日期不能晚于侧边栏里的研究日。")
        st.stop()
    st.write(f"将使用策略 {saved[0]} 第 {saved[1]} 版，费用 {chosen}，{start} 至 {end}，资金 {cash}。")
    if st.button("确认后运行回测"):
        profile_id, cost_version = (int(part) for part in chosen.split(":"))
        try:
            actual = run_factor_backtest(identity, saved[0], saved[1], profile_id, cost_version, "SYNTH300", start, end, Decimal(str(cash)), as_of=as_of)
            zero = run_factor_backtest(identity, saved[0], saved[1], profile_id, cost_version, "SYNTH300", start, end, Decimal(str(cash)), zero_cost=True, as_of=as_of)
            st.session_state[f"d_strategy_bt_{identity.user_id}"] = (actual, zero)
            st.success(f"已提交实费回测 {actual}，独立零成本对照 {zero}。可取消，或在完成后读取。")
        except Exception as exc:
            st.error(str(exc).split("CONTEXT:")[0])
shown = st.session_state.get(f"d_strategy_bt_{identity.user_id}")
if shown and st.button("取消回测"):
    with transaction(user_id=identity.user_id) as conn:
        conn.execute("UPDATE research.factor_bt_run SET status='CANCELLED' WHERE bt_id = ANY(%s) AND status='RUNNING'", (list(shown),))
    st.warning("已要求取消。不会再生成新的成交。")
if shown and st.button("读取回测"):
    for bt_id in shown:
        wait_backtest(identity, bt_id)
if shown:
    frames = []
    for bt_id, label in ((shown[0], "实费"), (shown[1], "零成本对照")):
        with transaction(user_id=identity.user_id, read_only=True) as conn:
            points = conn.execute(
                "SELECT trade_date, net_equity, gross_same_fills, cumulative_fees FROM research.backtest_pnl WHERE bt_id=%s ORDER BY trade_date",
                (bt_id,),
            ).fetchall()
        if points:
            frame = pd.DataFrame(points, columns=["日期", "净权益", "同成交毛权益", "累计费用"])
            frame["口径"] = label
            frames.append(frame)
    if frames:
        merged = pd.concat(frames, ignore_index=True)
        wide = merged.pivot(index="日期", columns="口径", values="净权益").reset_index()
        columns = [column for column in wide.columns if column != "日期"]
        st.altair_chart(time_series_chart(wide, "日期", columns, "净权益（元）", value_format=".2f"), width="stretch")
        st.dataframe(merged, hide_index=True)
