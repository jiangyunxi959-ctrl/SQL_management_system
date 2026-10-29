"""个人费用。保存后生成新版本，不改旧回测。"""
import streamlit as st

from app.costs.service import list_costs, save_cost
from app.ui.session import require_identity

st.title("个人成本设置")
st.caption("这里的数字只用于本人模拟。页面上的测试例子不是现行市场费率。")
identity, _as_of = require_identity()
name = st.text_input("方案名称", "我的A股费用", key=f"d_cost_name_{identity.user_id}")
mode = st.selectbox("模式", ["CN_A", "CUSTOM_LIQUIDITY"], key=f"d_cost_mode_{identity.user_id}")
commission = st.number_input("佣金率（未分别填写时的备用）", value=0.0003, format="%.6f", key=f"d_cost_commission_{identity.user_id}")
buy_commission = st.number_input("买入佣金率", value=0.0003, format="%.6f", key=f"d_cost_buy_{identity.user_id}")
sell_commission = st.number_input("卖出佣金率", value=0.0003, format="%.6f", key=f"d_cost_sell_{identity.user_id}")
minimum = st.number_input("最低佣金", value=5.0, format="%.2f", key=f"d_cost_min_{identity.user_id}")
transfer = st.number_input("过户费率", value=0.00001, format="%.6f", key=f"d_cost_transfer_{identity.user_id}")
stamp = st.number_input("印花税率（卖方）", value=0.0005, format="%.6f", key=f"d_cost_stamp_{identity.user_id}")
maker = st.number_input("maker 费率", value=0.0, format="%.6f", key=f"d_cost_maker_{identity.user_id}")
taker = st.number_input("taker 费率", value=0.0, format="%.6f", key=f"d_cost_taker_{identity.user_id}")
liquidity = st.selectbox("流动性假设", ["ALL_TAKER", "ALL_MAKER"], key=f"d_cost_liquidity_{identity.user_id}")
slippage = st.number_input("滑点（基点）", value=0.0, format="%.2f", key=f"d_cost_slip_{identity.user_id}")
if st.button("保存为新版本"):
    try:
        profile_id, version = save_cost(identity, name, {
            "mode": mode,
            "commission_rate": commission,
            "buy_commission_rate": buy_commission,
            "sell_commission_rate": sell_commission,
            "min_commission": minimum,
            "transfer_rate": transfer,
            "stamp_rate": stamp,
            "maker_rate": maker,
            "taker_rate": taker,
            "liquidity": liquidity,
            "slippage_bps": slippage,
        })
        st.session_state[f"d_cost_saved_{identity.user_id}"] = (profile_id, version)
        st.success(f"已保存方案 {profile_id} 第 {version} 版。")
    except Exception as exc:
        st.error(str(exc).split("CONTEXT:")[0])
rows = list_costs(identity)
st.dataframe(rows, hide_index=True)
