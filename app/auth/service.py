"""登录会话。正式入口只接收会话凭证，不接收手填的用户编号。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from psycopg.rows import dict_row

from app.audit.log import begin, finish
from app.auth.passwords import hash_password, verify_password
from app.db import connect_admin, transaction

SESSION_HOURS = 12


class LoginError(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    user_id: int
    username: str
    display_name: str
    token_hash: str


def create_user(username: str, display_name: str, password: str, role: str = "LEARNER") -> int:
    if role not in {"LEARNER", "ADMIN", "AUDITOR"}:
        raise ValueError("用户角色不合法")
    digest = hash_password(password)
    with connect_admin() as conn:
        user_id = conn.execute(
            """
            INSERT INTO core.app_user(username, display_name, role)
            VALUES (%s, %s, %s) RETURNING user_id
            """,
            (username, display_name, role),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO core.user_credential(user_id, password_hash) VALUES (%s, %s)",
            (user_id, digest),
        )
        conn.commit()
    return user_id


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def login(username: str, password: str) -> str:
    request = begin(None, None, "auth.login")
    with transaction(role="auth", read_only=True) as conn:
        row = conn.execute("SELECT * FROM core.fn_password_hash(%s)", (username,)).fetchone()
    verified = verify_password(row[1] if row else None, password)
    if row is None or not verified:
        finish(request, "REJECTED", user_id=None, as_of=None, tool="auth.login",
               summary=None, error_class="LOGIN")
        raise LoginError("用户名或密码错误")
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    with transaction(role="auth") as conn:
        conn.execute(
            "SELECT core.fn_open_session(%s, %s, %s)",
            (row[0], _token_hash(token), expires),
        )
    finish(request, "SUCCESS", user_id=row[0], as_of=None, tool="auth.login",
           summary="login", error_class=None)
    return token


def resolve(token: str | None) -> Identity:
    if not token:
        raise LoginError("请先登录")
    digest = _token_hash(token)
    with transaction(role="app", read_only=True) as conn:
        row = conn.execute("SELECT * FROM core.fn_session_user(%s)", (digest,)).fetchone()
    if row is None:
        raise LoginError("登录已失效，请重新登录")
    return Identity(int(row[0]), row[1], row[2], digest)


def logout(token: str) -> None:
    digest = _token_hash(token)
    with transaction(role="app") as conn:
        conn.execute("SELECT core.fn_revoke_session(%s)", (digest,))


def current_identity(conn_factory=None):
    """给页面使用的解析入口。"""
    return resolve
