from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import streamlit as st

from app.auth import LoginError, login

st.title("登录")
st.caption("使用用户名和密码。页面不会要求填写用户编号或数据库密码。")
if st.session_state.get("auth_token"):
    st.success("已经登录。可以从左侧进入历史资料、公式、模拟交易和操作记录。")
else:
    with st.form("login"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        if st.form_submit_button("登录"):
            try:
                st.session_state["auth_token"] = login(username, password)
                st.rerun()
            except LoginError as exc:
                st.error(str(exc))
