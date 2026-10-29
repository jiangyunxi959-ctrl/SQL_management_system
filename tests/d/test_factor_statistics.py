from datetime import date
from decimal import Decimal
import math
from pathlib import Path

import pytest

from app.agent.gateway import handle, ScriptedModel
from app.db import transaction
from app.factors.service import list_definitions
from app.factors.execution import save_run
from app.factors.statistics import summarize_run


def saved(identity, values, reasons=None):
    factor = next(row for row in list_definitions(identity) if row[2]=='VWAP_D')
    payload=[]
    for index,value in enumerate(values):
        codes = (reasons[index] if reasons else ['MISSING_DATA']) if value is None else []
        payload.append(dict(stock_code=f'sh.{600001+index}',trade_date='2025-05-30',
                            value=None if value is None else str(value), quality='MISSING' if value is None else 'OK',
                            reason_codes=codes,reason_detail='test' if codes else None))
    with transaction(user_id=identity.user_id,as_of=date(2025,5,30)) as conn:
        return save_run(conn,factor[0],1,date(2025,5,30),'SYNTH300','statistics-fixture',payload)


def test_distribution_and_reason_counts(identities):
    person=identities[0][0]
    run=saved(person,[-2,0,2,4,None,None],[[],[],[],[],['MISSING_DATA'],['SUSPENDED','ZERO_VOLUME']])
    stats=summarize_run(person,run)
    assert (stats['total_count'],stats['valid_count'],stats['missing_count'])==(6,4,2)
    assert stats['mean']==Decimal(1) and stats['median']==1
    assert float(stats['stddev'])==pytest.approx(math.sqrt(20/3))
    assert (stats['minimum'],stats['maximum'])==(-2,4)
    assert [stats[k] for k in ('p05','p25','p75','p95')]==pytest.approx([-1.7,-0.5,2.5,3.7])
    assert (stats['positive_count'],stats['negative_count'],stats['zero_count'])==(2,1,1)
    assert stats['valid_ratio']==pytest.approx(2/3)
    assert stats['reason_counts']=={'MISSING_DATA':1,'SUSPENDED':1,'ZERO_VOLUME':1}


@pytest.mark.parametrize('values,valid,median,stddev', [([],0,None,None),([None,None],0,None,None),([7],1,7,None),([3,3,3],3,3,0)])
def test_empty_missing_single_and_constant(identities,values,valid,median,stddev):
    person=identities[0][0]
    stats=summarize_run(person,saved(person,values))
    assert stats['valid_count']==valid
    assert stats['total_count']==len(values)
    assert stats['median']==median and stats['stddev']==stddev
    if not values:
        assert stats['valid_ratio'] is None


def test_statistics_and_tool_do_not_expose_another_users_run(identities):
    first,second=identities[0][0],identities[1][0]
    run=saved(first,[1,2,3])
    result=handle(first,date(2025,5,30),'统计',ScriptedModel([{'tool':'factor_statistics','args':{'run_id':run}}]))
    assert result['calls'][0]['result']['mean']==2
    with pytest.raises(ValueError,match='不存在或无权'):
        summarize_run(second,run)
    with pytest.raises(ValueError,match='不存在或无权'):
        handle(second,date(2025,5,30),'统计',ScriptedModel([{'tool':'factor_statistics','args':{'run_id':run}}]))
    with pytest.raises(ValueError,match='不存在或无权'):
        summarize_run(first,9223372036854775807)


def test_non_finite_values_are_not_included(identities):
    person=identities[0][0]
    stats=summarize_run(person,saved(person,[1,'Infinity','-Infinity']))
    assert (stats['valid_count'],stats['missing_count'])==(1,2)
    assert stats['mean']==1 and stats['median']==1 and stats['stddev'] is None
    assert stats['reason_counts']=={'NON_FINITE_RESULT':2}


def test_statistics_page_displays_saved_run_counts(identities):
    from streamlit.testing.v1 import AppTest
    person,token=identities[0]
    run=saved(person,[-2,0,2,4,None,None])
    app=AppTest.from_file(str(Path(__file__).resolve().parents[2]/'app/ui/pages/d_factors.py'))
    app.session_state['auth_token']=token
    app.run(timeout=30)
    app.session_state[f'd_pick_public_run_{person.user_id}']=run
    app.run(timeout=30)
    assert not app.exception and not app.error
    metrics={item.label:item.value for item in app.metric}
    assert metrics=={'股票总数':'6','有效数量':'4','缺失数量':'2'}
