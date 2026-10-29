from datetime import datetime, timedelta, timezone

import pytest

from app.auth import LoginError, login, logout, resolve
from app.auth.service import _token_hash
from app.db import connect_admin, transaction


def test_login_logout_and_same_error_for_unknown_user(identities):
    identity, token = identities[0]
    assert resolve(token).user_id == identity.user_id
    logout(token)
    with pytest.raises(LoginError, match="重新登录"):
        resolve(token)
    with pytest.raises(LoginError, match="用户名或密码错误"):
        login(identity.username, "wrong-pass-1")
    with pytest.raises(LoginError, match="用户名或密码错误"):
        login("missing_" + identity.username, "wrong-pass-1")


def test_expired_session_is_rejected(identities):
    identity, token = identities[0]
    digest = _token_hash(token)
    with connect_admin() as conn:
        conn.execute(
            "UPDATE core.auth_session SET expires_at=%s WHERE token_hash=%s",
            (datetime.now(timezone.utc) - timedelta(minutes=1), digest),
        )
        conn.commit()
    with pytest.raises(LoginError, match="重新登录"):
        resolve(token)


def test_app_user_cannot_read_password_hash(identities):
    identity, _token = identities[0]
    with pytest.raises(Exception):
        with transaction(user_id=identity.user_id, read_only=True) as conn:
            conn.execute("SELECT password_hash FROM core.user_credential")
