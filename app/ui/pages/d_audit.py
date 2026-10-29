import pandas as pd
import streamlit as st

from app.db import transaction
from app.ui.session import require_identity

st.title("操作记录")
identity, _as_of = require_identity()
with transaction(user_id=identity.user_id, read_only=True) as conn:
    requests = conn.execute(
        """
        SELECT request_id::text, event_seq, event_type, as_of, tool_name, result_summary, error_class, created_at
        FROM audit.request_event ORDER BY created_at DESC, event_seq
        LIMIT 200
        """
    ).fetchall()
    business = conn.execute(
        """
        SELECT event_id, event_type, object_type, object_id, created_at
        FROM audit.business_event ORDER BY event_id DESC LIMIT 100
        """
    ).fetchall()
st.subheader("请求")
st.dataframe(pd.DataFrame(requests, columns=["请求", "序号", "结果", "研究日", "动作", "摘要", "类别", "时间"]), hide_index=True)
st.subheader("已提交的业务")
st.dataframe(pd.DataFrame(business, columns=["编号", "动作", "对象", "对象编号", "时间"]), hide_index=True)
st.caption("这里只显示当前登录用户的记录。失败和被拒绝的请求也会留下。")
