"""知时统一入口。正式页面不提供用户编号或数据库密码输入。"""
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT))
st.set_page_config(page_title="知时", layout="wide")
pages = st.navigation(
    [
        st.Page(str(ROOT / "app/ui/pages/d_login.py"), title="登录", default=True),
        st.Page(str(ROOT / "app/ui/pages/d_history.py"), title="历史资料"),
        st.Page(str(ROOT / "app/ui/pages/d_factors.py"), title="公式"),
        st.Page(str(ROOT / "app/ui/pages/d_factor_research.py"), title="因子研究"),
        st.Page(str(ROOT / "app/ui/pages/d_factor_strategy.py"), title="因子策略"),
        st.Page(str(ROOT / "app/ui/pages/d_costs.py"), title="个人成本设置"),
        st.Page(str(ROOT / "app/ui/pages/d_ai.py"), title="AI分析"),
        st.Page(str(ROOT / "app/ui/pages/d_trading.py"), title="模拟交易"),
        st.Page(str(ROOT / "app/ui/pages/d_backtest.py"), title="原策略"),
        st.Page(str(ROOT / "app/ui/pages/d_audit.py"), title="操作记录"),
    ]
)
pages.run()
