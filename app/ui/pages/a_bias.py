"""A 的独立演示页；D 可直接纳入自己的 Streamlit 导航。"""

import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
from psycopg.rows import dict_row
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")


def read_at(as_of, code):
    """页面只用 agent_reader 登录；研究日仅在当前只读事务有效。"""
    with psycopg.connect(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "zhishi"),
        user="agent_reader",
        password=os.environ["AGENT_READER_PASSWORD"],
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                "SELECT set_config('app.as_of_date',%s,true)", (as_of.isoformat(),)
            )
            cur.execute("SELECT * FROM pit.fn_fundamentals(%s,4)", (code,))
            financials = cur.fetchall()
            cur.execute("SELECT * FROM pit.fn_index_members('SYNTH300',%s)", (as_of,))
            members = cur.fetchall()
            cur.execute("SELECT pit.market_date() AS date")
            market_date = cur.fetchone()["date"]
    return financials, members, market_date


st.set_page_config(page_title="知时 · 偏差演示", layout="wide")
st.title("回到当时，数据库能看到什么？")
st.caption("B+A 合成样本演示。更换研究日期，观察财报版本与指数成员如何变化。")
as_of = st.date_input(
    "研究日期（日终）",
    date(2025, 5, 1),
    min_value=date(2024, 1, 2),
    max_value=date(2025, 12, 31),
)
st.write(
    "试试：4 月 27 日尚未披露；5 月 1 日利润为 1 亿元；8 月 21 日更正为 8000 万元；8 月 30 日可见周六新财报。"
)
try:
    financials, members, market_date = read_at(as_of, "sh.600001")
    st.write(f"最近交易日：{market_date}；当时指数成员：{len(members)} 只。")
    st.subheader("样例公司 sh.600001 的当时财报")
    if financials:
        frame = pd.DataFrame(financials)[
            ["report_period", "announce_date", "net_profit", "roe", "source_kind"]
        ]
        frame.columns = ["报告期", "公告日", "净利润（元）", "ROE（小数）", "来源类型"]
        st.dataframe(frame, hide_index=True)
    else:
        st.info("这个日期还没有可见财报。")
    with st.expander("展开当时的指数成员"):
        st.dataframe(pd.DataFrame(members), hide_index=True)
except (psycopg.Error, KeyError) as error:
    st.error("数据库尚未准备好，请按 README_A.md 完成建库、样本导入和角色密码配置。")
    # 不把连接串或底层认证信息显示到页面。

st.subheader("同一组价格，为什么会得到两种结果？")
st.write(
    "以下曲线固定在 2025 年 5 月 1 日选股，次日收盘入场，持有至 6 月 30 日。切换上方研究日期不会重算此对照实验。"
)
for key, label, explanation in [
    ("E6", "前视偏差", "错误方式提前用上 8 月更正的财报，改变 5 月 ROE ≥ 9% 的选股。"),
    (
        "E7",
        "幸存者偏差",
        "错误方式用年底成员替换 5 月成员，把未来的退出和加入提前用上。",
    ),
]:
    st.write(f"**{key} · {label}**：{explanation}")
    path = ROOT / "experiments/a_bias" / f"{key}_nav.csv"
    if path.exists():
        curve = pd.read_csv(path, index_col=0)
        curve.index = pd.to_datetime(curve.index)
        curve.columns = ["只使用当时信息", "使用未来信息"]
        st.line_chart(curve)
        with st.expander("查看两种方式的选股差异"):
            st.dataframe(
                pd.read_csv(path.with_name(f"{key}_selection.csv")), hide_index=True
            )
    else:
        st.info("先运行 python experiments/a_bias/run.py 生成曲线。")
results = ROOT / "experiments/a_bias/results.md"
if results.exists():
    st.markdown(results.read_text(encoding="utf-8"))
