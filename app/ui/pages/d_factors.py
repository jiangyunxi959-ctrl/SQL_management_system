import pandas as pd
import streamlit as st

from app.factors.errors import FactorError
from app.factors.service import archive_private, copy_public, create_private, list_definitions, new_version, read_values, run_factor
from app.factors.validate import validate
from app.ui.session import require_identity
from app.ui.factor_statistics import render_statistics

st.title("公式")
identity, as_of = require_identity()
rows = list_definitions(identity)
public = [row for row in rows if row[1] == "SYSTEM_PUBLIC" and row[5] == 1]
private = [row for row in rows if row[1] == "USER_PRIVATE"]
st.subheader("公共目录")
categories = ["全部"] + sorted({row[15] for row in public if len(row) > 15 and row[15]})
category = st.selectbox("分类", categories, key=f"d_pick_category_{identity.user_id}")
keyword = st.text_input("搜索代码或名称", key=f"d_pick_keyword_{identity.user_id}")
visible = []
for row in public:
    row_category = row[15] if len(row) > 15 else None
    if category != "全部" and row_category != category:
        continue
    if keyword and keyword not in f"{row[2] or ''} {row[3]}":
        continue
    visible.append(row)
st.caption(f"当前显示 {len(visible)} / {len(public)} 项。窗口变体不是 50 种独立研究发现；未列入的来源条目不能运行。")
st.dataframe(pd.DataFrame(
    [(row[2], row[3], row[15] if len(row) > 15 else "", row[6], row[7], row[8], row[10]) for row in visible],
    columns=["代码", "名称", "分类", "表达式", "单位", "历史长度", "改编说明"],
), hide_index=True)
public_ids = [row[0] for row in visible]
if public_ids:
    key = f"d_pick_public_{identity.user_id}"
    current = st.session_state.get(key)
    chosen = st.selectbox("查看公共公式", public_ids, index=public_ids.index(current) if current in public_ids else 0,
                          format_func=lambda item: next(row[3] for row in visible if row[0] == item), key=key)
    selected = next(row for row in visible if row[0] == chosen)
    st.write(selected[9])
    public_pool = st.text_input('公共公式股票池', 'SYNTH300', key=f'd_pick_public_pool_{identity.user_id}')
    if st.button('按研究日计算公共公式'):
        try:
            public_run, _ = run_factor(identity, selected[0], selected[5], public_pool, as_of)
            st.session_state[f'd_pick_public_run_{identity.user_id}'] = public_run
            st.success(f'公共公式运行 {public_run} 已完成，结果仅本人可见。')
        except Exception as exc:
            st.error(str(exc).split('CONTEXT:')[0])
    public_run = st.session_state.get(f'd_pick_public_run_{identity.user_id}')
    if public_run:
        render_statistics(identity, public_run)
        st.dataframe(pd.DataFrame(read_values(identity, public_run),
                                 columns=['证券', '交易日', '数值', '状态', '原因码', '说明']), hide_index=True)
    copy_name = st.text_input("复制后的私有名称", f"{selected[3]}副本", key=f"d_copy_name_{identity.user_id}")
    if st.button("复制到我的公式"):
        try:
            copy_public(identity, selected[0], 1, copy_name)
            st.success("已复制。之后公共公式变化不会改变这份副本。")
            st.rerun()
        except Exception as exc:
            st.error(str(exc).split("CONTEXT:")[0])

st.subheader("我的公式")
expression = st.text_area("表达式", "close_raw / (amount / volume) - 1")
if st.button("检查表达式"):
    try:
        checked = validate(expression)
        st.success(f"{checked.explanation}。单位：{checked.unit_label}。需要 {checked.lookback} 个交易日。")
    except FactorError as exc:
        st.error(f"{exc.message}（第 {exc.start + 1}–{exc.end} 个字符）")
name = st.text_input("私有公式名称", "收盘相对成交均价")
if st.button("保存为新公式"):
    try:
        create_private(identity, name, expression)
        st.success("已保存为第 1 版。")
        st.rerun()
    except FactorError as exc:
        st.error(f"{exc.message}（第 {exc.start + 1}–{exc.end} 个字符）")
    except Exception as exc:
        st.error(str(exc).split("CONTEXT:")[0])
if private:
    ids = []
    labels = {}
    for row in private:
        if row[0] not in labels:
            ids.append(row[0])
            labels[row[0]] = f"{row[3]}（{'已归档' if row[4] else '使用中'}）"
    key = f"d_pick_private_{identity.user_id}"
    current = st.session_state.get(key)
    factor_id = st.selectbox("我的公式", ids, index=ids.index(current) if current in ids else 0,
                             format_func=lambda item: labels[item], key=key)
    versions = [row for row in private if row[0] == factor_id]
    st.dataframe(pd.DataFrame(
        [(row[5], row[6], row[7], row[8]) for row in versions],
        columns=["版本", "表达式", "单位", "历史长度"],
    ), hide_index=True)
    version_no = st.selectbox("用于计算的版本", [row[5] for row in versions], key=f"d_pick_version_{identity.user_id}_{factor_id}")
    if st.button("把当前表达式存成新版本"):
        try:
            created = new_version(identity, factor_id, expression)
            st.success(f"已保存第 {created} 版，旧版本保持不变。")
            st.rerun()
        except Exception as exc:
            st.error(str(exc).split("CONTEXT:")[0])
    if st.button("归档"):
        archive_private(identity, factor_id)
        st.success("已归档。旧版本和旧结果仍可查看。")
        st.rerun()
    if st.button("用这个固定版本做因子研究"):
        st.session_state[f"d_eval_factor_{identity.user_id}"] = factor_id
        st.session_state[f"d_eval_version_{identity.user_id}"] = int(version_no)
        st.success("已带入因子研究页，版本不会随后续编辑改变。")
    universe = st.text_input("股票池", "SYNTH300")
    threshold = st.number_input("筛选阈值（可留空比较）", value=0.0, format="%.4f")
    direction = st.selectbox("和阈值比较", ["不筛选", "大于等于", "小于等于"])
    order = st.selectbox("排序", ["从高到低", "从低到高"])
    if st.button("按研究日计算"):
        try:
            run_id, _plan = run_factor(identity, factor_id, int(version_no), universe, as_of)
            st.session_state[f"d_pick_run_{identity.user_id}"] = run_id
            st.success(f"运行 {run_id} 已完成。")
        except FactorError as exc:
            st.error(exc.message)
        except Exception as exc:
            st.error(str(exc).split("CONTEXT:")[0])
    run_key = f"d_pick_run_{identity.user_id}"
    if st.session_state.get(run_key):
        render_statistics(identity, st.session_state[run_key])
        values = read_values(identity, st.session_state[run_key])
        frame = pd.DataFrame(values, columns=["证券", "交易日", "数值", "状态", "原因码", "说明"])
        if direction != "不筛选":
            numeric = pd.to_numeric(frame["数值"], errors="coerce")
            mask = numeric >= threshold if direction == "大于等于" else numeric <= threshold
            frame = frame[mask.fillna(False)]
        frame = frame.sort_values("数值", ascending=order == "从低到高", na_position="last")
        st.dataframe(frame, hide_index=True)
        stocks = frame["证券"].tolist()
        if stocks:
            stock = st.selectbox("带入模拟交易的证券", stocks, key=f"d_pick_stock_{identity.user_id}")
            if st.button("只预填股票，不自动下单"):
                st.session_state[f"d_prefill_{identity.user_id}"] = stock
                st.success(f"已预填 {stock}。请到模拟交易页亲自提交。")
