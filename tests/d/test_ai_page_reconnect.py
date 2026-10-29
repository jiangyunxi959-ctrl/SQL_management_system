"""A temporary history-read failure must not expose a traceback or break recovery."""
from pathlib import Path

import pytest
from psycopg import InterfaceError, OperationalError
from streamlit.testing.v1 import AppTest

import app.agent.service as service

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('connection_error',[OperationalError,InterfaceError])
def test_ai_history_disconnect_displays_retry_and_recovers(identities,monkeypatch,connection_error):
    _owner,token=identities[0]
    real_history=service.thread_turns

    def unavailable(*args,**kwargs):
        raise connection_error('temporary connection loss')

    monkeypatch.setattr(service,'thread_turns',unavailable)
    app=AppTest.from_file(str(ROOT/'app/ui/pages/d_ai.py'))
    app.session_state['auth_token']=token
    app.run(timeout=30)
    assert not app.exception
    assert any('数据库连接暂时中断' in item.value for item in app.info)

    monkeypatch.setattr(service,'thread_turns',real_history)
    app.run(timeout=30)
    assert not app.exception
    assert not any('数据库连接暂时中断' in item.value for item in app.info)
    assert any(item.label=='问题' for item in app.text_area)
