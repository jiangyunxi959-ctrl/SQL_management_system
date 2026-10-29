from decimal import Decimal
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from app.ui.charts import time_series_chart


def test_decimal_prices_and_small_nav_changes_have_numeric_scaled_axes():
    for values in ([Decimal('14.653700000000'),Decimal('14.692500000000')], [1.0,1.0585888], [1.,1.]):
        spec=time_series_chart(pd.DataFrame({'日期':['2025-05-29','2025-05-30'],'值':values}),'日期',['值'],'测试').to_dict()
        axis=spec['encoding']['y']
        assert axis['type']=='quantitative'
        low,high=axis['scale']['domain']
        assert low<float(min(values))<=float(max(values))<high
        if float(min(values))>=1:
            assert low>0


def test_history_displays_existing_financial_record_instead_of_empty_columns(identities):
    _,token=identities[0]
    app=AppTest.from_file(str(Path(__file__).resolve().parents[2]/'app/ui/pages/d_history.py'))
    app.session_state['auth_token']=token
    app.run(timeout=30)
    next(item for item in app.selectbox if item.label=='证券').select('sh.600004').run(timeout=30)
    assert not app.exception and not app.error
    frames=[item.value for item in app.dataframe]
    financial=next(frame for frame in frames if '净利润（元）' in frame.columns)
    assert float(financial.iloc[0]['净利润（元）'])==100000000
    assert financial.iloc[0]['来源类型']=='SYNTHETIC'
