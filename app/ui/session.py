"""统一页面的登录状态。切换用户时清掉上一个用户的选择。"""
from datetime import date
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st

from app.auth import LoginError, resolve


def require_identity():
    token = st.session_state.get("auth_token")
    try:
        identity = resolve(token)
    except LoginError as exc:
        st.warning(str(exc))
        st.stop()
    previous = st.session_state.get("bound_user")
    if previous != identity.user_id:
        for key in list(st.session_state):
            if key.startswith(("d_pick_", "d_account_", "d_code_", "d_prefill_", "d_ai_", "d_eval_", "d_cost_", "d_strategy_", "c_last_run")):
                del st.session_state[key]
        st.session_state["bound_user"] = identity.user_id
    st.sidebar.caption(f"当前用户：{identity.display_name}")
    default_day = date.today() if "real" in os.getenv("PGDATABASE", "") else date(2025, 5, 30)
    as_of = st.sidebar.date_input("研究日期（日终）", value=st.session_state.get("as_of", default_day))
    st.session_state["as_of"] = as_of
    if st.sidebar.button("退出登录"):
        from app.agent.jobs import cancel_owner
        from app.factor_research.service import cancel_owner_jobs
        cancel_owner(identity)
        cancel_owner_jobs(identity)
        from app.auth import logout
        logout(token)
        st.session_state.pop("auth_token", None)
        st.session_state.pop("bound_user", None)
        for key in list(st.session_state):
            if key.startswith(("d_pick_", "d_account_", "d_code_", "d_prefill_", "d_ai_", "d_eval_", "d_cost_", "d_strategy_", "c_last_run")):
                del st.session_state[key]
        st.rerun()
    return identity, as_of
