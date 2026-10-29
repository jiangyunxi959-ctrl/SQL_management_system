"""因子研究。评价在后台运行，页面只读取已保存的结果。"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from app.factor_research.service import cancel_eval, report, start_evaluation
from app.factors.service import list_definitions
from app.ui.session import require_identity

st.title("因子研究")
identity, as_of = require_identity()
rows = [row for row in list_definitions(identity) if not row[4] and row[1] in {"USER_PRIVATE", "SYSTEM_PUBLIC"}]
if not rows:
    st.info("先在公式页保存一份私有公式。")
    st.stop()
labels = {}
ids = []
for row in rows:
    if row[0] not in labels:
        ids.append(row[0])
        labels[row[0]] = f"{row[3]}（{'公共' if row[1] == 'SYSTEM_PUBLIC' else '私有'}）"
carried = st.session_state.get(f"d_eval_factor_{identity.user_id}")
factor_id = st.selectbox(
    "公式",
    ids,
    index=ids.index(carried) if carried in ids else 0,
    format_func=lambda item: labels[item],
    key=f"d_eval_factor_pick_{identity.user_id}",
)
versions = [row[5] for row in rows if row[0] == factor_id]
carried_version = st.session_state.get(f"d_eval_version_{identity.user_id}")
version = st.selectbox(
    "固定版本",
    versions,
    index=versions.index(carried_version) if carried_version in versions else len(versions) - 1,
    key=f"d_eval_version_pick_{identity.user_id}",
)
start = st.date_input("开始", value=as_of - timedelta(days=20), key=f"d_eval_start_{identity.user_id}")
end = st.date_input("结束", value=as_of, key=f"d_eval_end_{identity.user_id}")
if st.button("提交评价"):
    try:
        eval_id = start_evaluation(identity, factor_id, int(version), "SYNTH300", start, end, as_of=as_of)
        st.session_state[f"d_eval_run_{identity.user_id}"] = eval_id
        st.success(f"评价 {eval_id} 已提交。计算完成后点“读取结果”。")
    except Exception as exc:
        st.error(str(exc).split("CONTEXT:")[0])
eval_id = st.session_state.get(f"d_eval_run_{identity.user_id}")
if eval_id and st.button("取消评价"):
    cancel_eval(identity, int(eval_id))
    st.warning("已要求取消。正在计算的步骤结束后不再继续。")
if eval_id and st.button("读取结果"):
    try:
        data = report(identity, int(eval_id), as_of)
        st.write(f"状态：{data['status']}，股票池 {data['universe']}，{data['start']} 至 {data['end']}")
        st.json({str(key): {item: str(value) for item, value in value.items()} for key, value in data["summary"].items()})
        st.dataframe(pd.DataFrame(data["days"], columns=["信号日", "持有期", "候选", "有效", "IC", "Rank IC", "原因", "分组数"]), hide_index=True)
        if data.get("groups"):
            st.subheader("分组")
            st.caption("分组收益是理论等权标签，不是扣费后的组合。")
            st.dataframe(pd.DataFrame(data["groups"], columns=["信号日", "持有期", "组号", "人数", "平均标签", "原因"]), hide_index=True)
    except Exception as exc:
        st.error(str(exc).split("CONTEXT:")[0])
