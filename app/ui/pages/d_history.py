from datetime import timedelta
import os

import pandas as pd
import streamlit as st
from psycopg.rows import dict_row

from app.db import transaction
from app.ui.session import require_identity
from app.ui.charts import time_series_chart

st.title("历史资料")
identity, as_of = require_identity()
real_dataset = "real" in os.getenv("PGDATABASE", "")
try:
    with transaction(user_id=identity.user_id, as_of=as_of, read_only=True) as conn:
        market = conn.execute("SELECT pit.market_date()").fetchone()[0]
        if real_dataset:
            imported = conn.execute("SELECT DISTINCT stock_code FROM pit.fin_report ORDER BY stock_code").fetchall()
            codes = [row[0] for row in imported]
        else:
            members = conn.execute("SELECT * FROM pit.fn_index_members('SYNTH300', %s)", (as_of,)).fetchall()
            codes = [row[0] for row in members]
    if real_dataset:
        st.write(f"研究日 {as_of}。行情最近交易日 {market}。已导入且在该日可见的财报公司 {len(codes)} 家，不要求当时属于指数。")
        st.caption("采集日晚于行情截止日时，价格仍停在已有行情，不会补出新K线。把研究日调到采集日才能看到这份快照。")
    else:
        st.write(f"研究日 {as_of}，最近交易日 {market}，当时指数成员 {len(codes)} 只。")
    if not codes:
        st.info("这个研究日没有可选证券。" if not real_dataset else "这个研究日还没有已导入且可见的财报。")
    else:
        key = f"d_pick_history_{identity.user_id}"
        current = st.session_state.get(key)
        code = st.selectbox("证券", codes, index=codes.index(current) if current in codes else 0, key=key)
        with transaction(user_id=identity.user_id, as_of=as_of, read_only=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM pit.fn_fundamentals(%s, 4)", (code,))
                financials = cur.fetchall()
            prices = conn.execute(
                "SELECT trade_date, close FROM pit.fn_price_history(%s, %s, %s, 'hfq') ORDER BY trade_date",
                (code, as_of - timedelta(days=60), as_of),
            ).fetchall()
        st.subheader("当时可见的财报")
        if financials:
            frame = pd.DataFrame(financials)
            names = {"report_period":"报告期", "announce_date":"公告日", "available_date":"本库可用日",
                     "net_profit":"净利润（元）", "eps":"EPS", "eps_basis":"EPS口径", "period_basis":"期间口径",
                     "roe":"ROE（小数）", "source_kind":"来源类型"}
            show = [column for column in names if column in frame.columns]
            st.dataframe(frame[show].rename(columns=names), hide_index=True)
            if any(item['source_kind']=='SYNTHETIC' for item in financials):
                st.caption("SYNTHETIC 表示教学合成财报，不是外部公司公告。外部采集快照按实际可用日期展示，不能提前用于历史选股。")
        else:
            st.info("这个日期还没有可见财报。")
        if prices:
            st.subheader("价格走势（后复权，元/股）")
            st.caption("这里是每股收盘价格，不是公司市值。纵轴按当前可见价格范围显示。")
            chart = pd.DataFrame(prices, columns=["日期", "后复权收盘价"])
            st.altair_chart(time_series_chart(chart,"日期",["后复权收盘价"],"后复权收盘价（元/股）",value_format='.2f'),width='stretch')
except Exception as exc:
    st.error(str(exc).split("CONTEXT:")[0])
