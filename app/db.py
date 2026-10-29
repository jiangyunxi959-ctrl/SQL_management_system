"""共享事务。身份和日期只在当前事务有效，异常退出后也会清掉。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

ROLES = {
    "app": ("app_user", "APP_USER_PASSWORD"),
    "agent": ("agent_reader", "AGENT_READER_PASSWORD"),
    "etl": ("etl_writer", "ETL_WRITER_PASSWORD"),
    "auditor": ("auditor", "AUDITOR_PASSWORD"),
    "audit": ("audit_writer", "AUDIT_WRITER_PASSWORD"),
    "auth": ("auth_login", "AUTH_LOGIN_PASSWORD"),
    "checkpoint": ("checkpoint_writer", "CHECKPOINT_WRITER_PASSWORD"),
}


def connect_admin(dbname: str | None = None):
    return psycopg.connect(
        host=os.getenv("PGADMIN_HOST", "127.0.0.1"),
        port=int(os.getenv("PGADMIN_PORT", "5432")),
        user=os.getenv("PGADMIN_USER", "postgres"),
        password=os.environ["PGADMIN_PASSWORD"],
        dbname=dbname or os.getenv("PGDATABASE", "zhishi"),
    )


def _connect(role: str):
    username, password_key = ROLES[role]
    return psycopg.connect(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "zhishi"),
        user=username,
        password=os.environ[password_key],
    )


def _clear(conn) -> None:
    if conn.closed:
        return
    conn.rollback()
    conn.execute("SELECT set_config('app.user_id', '', false)")
    conn.execute("SELECT set_config('app.as_of_date', '', false)")
    conn.execute("SELECT set_config('app.data_snapshot_at', '', false)")
    conn.execute("SELECT set_config('app.request_id', '', false)")
    conn.execute("RESET statement_timeout")
    conn.commit()


@contextmanager
def transaction(*, user_id=None, as_of: date | None = None, read_only=False, role="app",
                isolation="read_committed", timeout_ms=120000, _connection=None):
    if role not in ROLES:
        raise ValueError("未知的数据库身份")
    if isolation not in {"read_committed", "repeatable_read"}:
        raise ValueError("不支持的隔离级别")
    owns = _connection is None
    conn = _connection or _connect(role)
    try:
        with conn.transaction():
            characteristics = []
            if isolation == "repeatable_read":
                characteristics.append("ISOLATION LEVEL REPEATABLE READ")
            if read_only:
                characteristics.append("READ ONLY")
            if characteristics:
                conn.execute("SET TRANSACTION " + ", ".join(characteristics))
            conn.execute("SELECT set_config('statement_timeout', %s, true)", [str(int(timeout_ms))])
            conn.execute(
                "SELECT set_config('app.user_id', %s, true)",
                ["" if user_id is None else str(int(user_id))],
            )
            conn.execute(
                "SELECT set_config('app.as_of_date', %s, true)",
                ["" if as_of is None else as_of.isoformat()],
            )
            conn.execute("SELECT set_config('app.data_snapshot_at', '', true)")
            yield conn
    finally:
        try:
            _clear(conn)
        finally:
            if owns:
                conn.close()
