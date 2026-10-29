from datetime import date
from threading import Event
from uuid import uuid4

import pytest
from psycopg.errors import ForeignKeyViolation, InsufficientPrivilege

from app.agent.deepseek import DeepSeekModel, ModelError, ScriptedLoop
from app.agent.evidence import check_draft
from app.agent.service import ask, save_preference, start_thread
from app.agent.tools import validate_arguments
from app.agent.contracts import ToolRejected
from app.auth import logout
from app.db import transaction


def answer(text='完成'):
    import json
    return {'role':'assistant','tool_calls':[], 'content':json.dumps({
        'answer':text,'claims':[],'citations':[],'expression_draft':None,'limitations':[]},ensure_ascii=False)}


def test_other_owner_cannot_inject_prompt_version(identities):
    owner,other=[item[0] for item in identities]
    save_preference(owner,'甲的提示词')
    save_preference(other,'乙的提示词')
    with transaction(user_id=owner.user_id,read_only=True) as c:
        profile=c.execute('SELECT profile_id FROM agent.prompt_profile').fetchone()[0]
    with pytest.raises(InsufficientPrivilege):
        with transaction(user_id=other.user_id) as c:
            c.execute("INSERT INTO agent.prompt_version(profile_id,version_no,content,digest) VALUES (%s,9999,'injected','x')",[profile])


def test_child_message_cannot_bind_to_other_owner_turn(identities):
    owner,other=[item[0] for item in identities]
    thread=start_thread(owner,date(2025,5,30),'SYNTH300')
    result=ask(owner,thread,'测试归属','request-'+uuid4().hex,ScriptedLoop([answer()]))
    with pytest.raises(ForeignKeyViolation):
        with transaction(user_id=other.user_id) as c:
            c.execute("INSERT INTO agent.message(turn_id,owner_id,seq,role,content) VALUES (%s,%s,999,'user','injected')",[result['turn_id'],other.user_id])


def test_next_turn_receives_history_and_does_not_reuse_old_answer(identities):
    identity=identities[0][0]
    thread=start_thread(identity,date(2025,5,30),'SYNTH300')
    first=ask(identity,thread,'记住这条问题','request-'+uuid4().hex,ScriptedLoop([answer('上一轮结论')]))
    assert first['answer']['answer']=='上一轮结论'
    class Broken:
        def complete(self,messages,tools):
            assert any(m.get('content')=='记住这条问题' for m in messages)
            assert any('2025-05-30' in m.get('content','') and m['role']=='system' for m in messages)
            raise ModelError('UNAVAILABLE','测试中断')
    second=ask(identity,thread,'接着解释','request-'+uuid4().hex,Broken())
    assert second['status']=='FAILED' and second['answer'] is None


def test_logout_invalidates_saved_identity(identities):
    identity,token=identities[0]
    logout(token)
    with pytest.raises(PermissionError):
        start_thread(identity,date(2025,5,30),'SYNTH300')


def test_cancelled_before_network_does_not_call_model(identities):
    identity=identities[0][0]
    thread=start_thread(identity,date(2025,5,30),'SYNTH300')
    event=Event();event.set()
    result=ask(identity,thread,'取消测试','request-'+uuid4().hex,ScriptedLoop([]),cancel_event=event)
    assert result['status']=='CANCELLED' and result['model_calls']==0 and result['tool_calls']==0


def test_provider_retries_obey_remaining_budget(monkeypatch):
    from app.agent import deepseek
    model=DeepSeekModel();model.api_key='local-test-key'
    calls=[]
    def unavailable(*args,**kwargs):
        calls.append(kwargs['timeout'])
        raise ModelError('UNAVAILABLE','temporary')
    monkeypatch.setattr(deepseek,'_post',unavailable)
    with pytest.raises(ModelError) as err:
        model.complete([],[],max_attempts=1,timeout=0.25)
    assert len(calls)==1 and err.value.attempts==1 and calls[0]<=0.25


@pytest.mark.parametrize('run_ids',[[True,2],['1',2],[0,2]])
def test_compare_rejects_invalid_array_items(run_ids):
    with pytest.raises(ToolRejected):
        validate_arguments('compare_factor_runs',{'run_ids':run_ids})


def test_missing_field_is_not_valid_null_evidence():
    draft={'answer':'无值','citations':['E1'],'claims':[{'evidence_id':'E1','path':'data.nonexistent','raw_value':None,'display_unit':'raw'}]}
    assert not check_draft(draft,{'E1':{'data':{}}})[0]


def test_citations_and_percent_dimensions_are_checked():
    assert not check_draft({'answer':'依据[E99]','citations':['E99'],'claims':[]},{})[0]
    draft={'answer':'价格','citations':['E1'],'claims':[{'evidence_id':'E1','path':'data.mean','raw_value':20,'display_unit':'percent'}]}
    assert not check_draft(draft,{'E1':{'data':{'mean':20}}})[0]


def test_list_evidence_paths_accept_bracket_and_dot_indices():
    for path in ('data[0].run_id','data.0.run_id'):
        draft={'answer':'运行已找到','citations':['E1'],'claims':[{'evidence_id':'E1','path':path,'raw_value':12,'display_unit':'count'}]}
        assert check_draft(draft,{'E1':{'data':[{'run_id':12}]}})[0]
    claim={'evidence_id':'E1','path':'left.valid_count','raw_value':19,'display_unit':'count'}
    assert check_draft({'answer':'已核对','citations':['E1'],'claims':[claim]}, {'E1':{'data':{'left':{'valid_count':19}}}})[0]
    assert claim['path']=='data.left.valid_count'


def test_provider_json_literal_newlines_are_safe_to_parse():
    from app.agent.evidence import parse_draft
    assert parse_draft('{"answer":"第一行\n第二行","citations":[],"claims":[]}')['answer']=='第一行\n第二行'


def test_return_percent_is_valid_but_price_percent_is_not():
    for unit,expected in [('无单位',True),('元/股（未调整价格）',False)]:
        draft={'answer':'已核对','citations':['E1'],'claims':[{'evidence_id':'E1','path':'data.median','raw_value':0.01,'display_unit':'percent'}]}
        assert check_draft(draft,{'E1':{'data':{'median':0.01,'unit_label':unit}}})[0] == expected


def test_checkpoint_role_is_separate_and_cannot_read_market_or_turns(identities):
    identity=identities[0][0]
    with transaction(user_id=identity.user_id,role='checkpoint',read_only=True) as c:
        row=c.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        assert row==('checkpoint_writer',False,False)
    for table in ('agent.turn','mkt.daily_bar'):
        with pytest.raises(InsufficientPrivilege):
            with transaction(user_id=identity.user_id,role='checkpoint',read_only=True) as c:
                from psycopg import sql
                c.execute(sql.SQL('SELECT * FROM {} LIMIT 1').format(sql.Identifier(*table.split('.'))))
