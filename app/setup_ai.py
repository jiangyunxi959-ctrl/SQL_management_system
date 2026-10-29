"""安装 D2 的对话、提示词和 50 项公共目录。不重建已有行情。"""
from __future__ import annotations

import os
import secrets
from dotenv import set_key
from psycopg import sql

from app.db import ROOT, connect_admin
from app.factors.catalog import register_d2

MIGRATION = ROOT / "sql" / "migrations" / "d2" / "001_ai_workflow.sql"
PANEL = ROOT / "sql" / "70_factor.sql"


def install(dbname: str | None = None) -> int:
    _checkpoint_role(dbname)
    with connect_admin(dbname) as conn:
        with conn.transaction():
            ready = conn.execute("SELECT to_regclass('factor.definition')").fetchone()[0]
            if ready is None:
                raise RuntimeError("请先安装 D，再安装 AI 工作流")
            conn.execute(PANEL.read_text(encoding="utf-8"))
            conn.execute(MIGRATION.read_text(encoding="utf-8"))
            count = register_d2(conn)
        conn.commit()
    return count


def _checkpoint_role(dbname):
    password = os.getenv('CHECKPOINT_WRITER_PASSWORD')
    if not password:
        password = secrets.token_urlsafe(32)
        set_key(ROOT / '.env','CHECKPOINT_WRITER_PASSWORD',password)
        os.environ['CHECKPOINT_WRITER_PASSWORD'] = password
    with connect_admin(dbname) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='checkpoint_writer'").fetchone():
            conn.execute('CREATE ROLE checkpoint_writer LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT')
        conn.execute(sql.SQL('ALTER ROLE checkpoint_writer PASSWORD {}').format(sql.Literal(password)))
        conn.commit()


if __name__ == "__main__":
    print(f"[OK] public factors {install()}")
