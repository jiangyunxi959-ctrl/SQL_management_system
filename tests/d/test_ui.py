from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.auth import create_user, login
import uuid

ROOT = Path(__file__).resolve().parents[2]


def test_formal_login_has_no_user_id_field():
    username = "ui_" + uuid.uuid4().hex[:8]
    create_user(username, "页面", "test-pass-1")
    app = AppTest.from_file(str(ROOT / "app/ui/pages/d_login.py")).run(timeout=30)
    assert not any(item.label.startswith("用户") and "编号" in item.label for item in app.number_input)
    app.text_input[0].set_value(username)
    app.text_input[1].set_value("test-pass-1")
    next(button for button in app.button if button.label == "登录").click().run(timeout=30)
    assert any("已经登录" in item.value for item in app.success)
    assert not app.exception
    token = login(username, "test-pass-1")
    factors = AppTest.from_file(str(ROOT / "app/ui/pages/d_factors.py"))
    factors.session_state["auth_token"] = token
    factors.run(timeout=30)
    assert not factors.exception
    factors.text_area[0].set_value("close_raw>10")
    next(button for button in factors.button if button.label == "检查表达式").click().run(timeout=30)
    assert any("筛选条件" in item.value for item in factors.error)
