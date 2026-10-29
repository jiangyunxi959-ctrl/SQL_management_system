"""Explicit numeric/time axes, with bounded padding around the observed values."""
import math

import altair as alt
import pandas as pd


def time_series_chart(frame, date_column, value_columns, y_title, *, baseline=None, value_format='.4f'):
    data = frame[[date_column, *value_columns]].copy()
    data[date_column] = pd.to_datetime(data[date_column])
    for column in value_columns:
        data[column] = pd.to_numeric(data[column], errors='coerce').astype(float)
        data.loc[~data[column].map(math.isfinite), column] = float('nan')
    values = data[value_columns].stack().dropna()
    if values.empty:
        raise ValueError('没有可绘图的有效数值')
    low, high = float(values.min()), float(values.max())
    if baseline is not None:
        low, high = min(low,baseline), max(high,baseline)
    padding = (high-low)*0.08 if high>low else max(abs(low)*0.01,0.01)
    long = data.melt(id_vars=[date_column],value_vars=value_columns,var_name='指标',value_name='数值')
    return alt.Chart(long).mark_line().encode(
        x=alt.X(f'{date_column}:T',title='日期',axis=alt.Axis(format='%Y-%m-%d',labelAngle=-30)),
        y=alt.Y('数值:Q',title=y_title,scale=alt.Scale(zero=False,domain=[low-padding,high+padding]),
                axis=alt.Axis(format=value_format)),
        color=alt.Color('指标:N',title=None),
        tooltip=[alt.Tooltip(f'{date_column}:T',title='日期',format='%Y-%m-%d'),
                 alt.Tooltip('指标:N'),alt.Tooltip('数值:Q',title=y_title,format=value_format)],
    ).properties(height=360)
