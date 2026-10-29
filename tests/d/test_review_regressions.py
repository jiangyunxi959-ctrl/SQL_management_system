from datetime import date
import uuid
from pathlib import Path

import pytest

from app.agent.gateway import handle
from app.db import transaction
from app.factors.service import list_definitions, run_factor
from app.trading import create_account
from app.trading.guard import submit_order


def test_request_key_cannot_reuse_other_factor_parameters(identities):
    person = identities[0][0]
    factor = next(r[0] for r in list_definitions(person) if r[2] == 'VWAP_D')
    key = uuid.uuid4().hex
    original, _ = run_factor(person, factor, 1, 'SYNTH300', date(2025, 5, 30), key)
    repeated, _ = run_factor(person, factor, 1, 'SYNTH300', date(2025, 5, 30), key)
    assert repeated == original
    with pytest.raises(ValueError):
        run_factor(person, factor, 1, 'SYNTH300', date(2025, 5, 29), key)
    account = create_account(person.user_id, 'review', date(2025, 5, 13), 100000)
    with pytest.raises(ValueError):
        submit_order(person, account, 'sh.600003', 'B', 'OPEN', 100, None, key)


def test_order_key_is_bound_to_original_parameters(identities):
    person = identities[0][0]
    account = create_account(person.user_id, 'review', date(2025, 5, 13), 100000)
    key = uuid.uuid4().hex
    first = submit_order(person, account, 'sh.600003', 'B', 'OPEN', 100, None, key)
    assert submit_order(person, account, 'sh.600003', 'B', 'OPEN', 100, None, key) == first
    with pytest.raises(ValueError):
        submit_order(person, account, 'sh.600003', 'B', 'OPEN', 200, None, key)


def test_model_error_leaves_failure_event(identities):
    person = identities[0][0]
    class BrokenModel:
        def plan(self, text):
            raise RuntimeError('review model unavailable')
    with pytest.raises(RuntimeError):
        handle(person, date(2025, 5, 30), 'review', BrokenModel())
    with transaction(user_id=person.user_id, read_only=True) as conn:
        events = conn.execute("SELECT event_type FROM audit.request_event WHERE tool_name='agent.handle' ORDER BY created_at,event_seq").fetchall()
    assert ('FAILURE',) in events


def test_public_formula_runs_without_copy_and_copy_refreshes(identities):
    from streamlit.testing.v1 import AppTest
    person, token = identities[0]
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / 'app/ui/pages/d_factors.py'))
    app.session_state['auth_token'] = token
    app.run(timeout=30)
    next(b for b in app.button if b.label == '按研究日计算公共公式').click().run(timeout=30)
    assert not app.exception and not app.error
    assert any('公共公式运行' in item.value for item in app.success)
    next(b for b in app.button if b.label == '复制到我的公式').click().run(timeout=30)
    assert not app.exception and not app.error
    assert any(item.label == '我的公式' for item in app.selectbox)
