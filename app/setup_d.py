"""把 D 安装到已有的 A/B/C 数据库，不重建、不删除行情和交易数据。"""
from __future__ import annotations

import os
from pathlib import Path
import secrets

from psycopg import sql
from dotenv import load_dotenv

from app.db import ROOT, connect_admin
from app.factors.catalog import register_public

FILES = ("70_factor.sql", "80_security_audit.sql", "90_agent_gateway.sql")
ROLES = ("audit_writer", "auth_login")
PASSWORDS = {"audit_writer": "AUDIT_WRITER_PASSWORD", "auth_login": "AUTH_LOGIN_PASSWORD"}


def ensure_secrets() -> None:
    missing = [key for key in PASSWORDS.values() if not os.getenv(key)]
    if not missing:
        return
    generated = {key: secrets.token_urlsafe(24) for key in missing}
    with (ROOT / ".env").open("a", encoding="utf-8") as handle:
        handle.write("\n# D 安装补充的本地角色密码\n")
        for key, value in generated.items():
            handle.write(f"{key}={value}\n")
            os.environ[key] = value
    load_dotenv(ROOT / ".env")


def ensure_roles(conn) -> None:
    for role in ROLES:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
        password = os.environ[PASSWORDS[role]]
        conn.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password))
        )


def install(dbname: str | None = None) -> None:
    ensure_secrets()
    admin = connect_admin(dbname)
    admin.autocommit = True
    try:
        ensure_roles(admin)
        required = admin.execute(
            """
            SELECT to_regclass('core.app_user'), to_regclass('mkt.daily_bar'),
                   to_regprocedure('pit.fn_screen(text,jsonb,text,int)'),
                   to_regclass('sim.account'), to_regclass('research.strategy')
            """
        ).fetchone()
        if any(item is None for item in required):
            raise RuntimeError("目标库还没有完整的 A/B/C 对象，不能安装 D")
    finally:
        admin.close()

    with connect_admin(dbname) as conn:
        with conn.transaction():
            for name in FILES:
                conn.execute((ROOT / "sql" / name).read_text(encoding="utf-8"))
            register_public(conn)
        conn.commit()


if __name__ == "__main__":
    install()
    print("[OK] D installed")
