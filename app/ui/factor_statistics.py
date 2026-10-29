"""公共和私有计算结果共用统计展示，统计范围固定为整次运行。"""
import pandas as pd
import streamlit as st

from app.factors.statistics import summarize_run
from app.factors.quality import REASON_BITS


def render_statistics(identity, run_id):
    summary = summarize_run(identity, run_id)
    st.subheader('因子统计')
    st.caption(f"运行 {run_id} · {summary['factor_name']} · 第 {summary['version_no']} 版 · "
               f"{summary['as_of']} · {summary['universe']}。统计整次运行，页面筛选不会改变此统计。")
    columns = st.columns(3)
    columns[0].metric('股票总数', summary['total_count'])
    columns[1].metric('有效数量', summary['valid_count'])
    columns[2].metric('缺失数量', summary['missing_count'])
    fields = [('均值','mean'),('中位数','median'),('样本标准差','stddev'),('最小值','minimum'),('最大值','maximum'),
              ('5%分位数','p05'),('25%分位数','p25'),('75%分位数','p75'),('95%分位数','p95')]
    st.dataframe(pd.DataFrame([(label, None if summary[key] is None else float(summary[key])) for label,key in fields],
                              columns=['统计量','数值']), hide_index=True)
    ratio = summary['valid_ratio']
    st.caption(f"单位：{summary['unit_label']}；有效比例：{f'{ratio:.1%}' if ratio is not None else '无样本'}。"
               f"正值 {summary['positive_count']}，负值 {summary['negative_count']}，零值 {summary['zero_count']}。"
               '空值不计入数值统计；样本不足两条时标准差为空。分位数采用线性插值。')
    if summary['reason_counts']:
        labels = {code:label for _,code,label in REASON_BITS}
        st.dataframe(pd.DataFrame([(labels.get(code,code),count) for code,count in sorted(summary['reason_counts'].items())],
                                  columns=['缺失原因','股票数量']),hide_index=True)
        st.caption('同一只股票可能有多个缺失原因，原因数量相加可能超过缺失股票数。')
    st.caption('这些统计描述因子值的分布，不代表因子的预测能力或策略收益。')
