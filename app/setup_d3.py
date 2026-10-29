"""安装 D3。页面导入不会执行这里的 DDL。"""
from __future__ import annotations

from app.db import ROOT, connect_admin

MIGRATION = ROOT / "sql" / "migrations" / "d3" / "001_research_cost.sql"


def install(dbname: str | None = None) -> None:
    with connect_admin(dbname) as conn:
        ready = conn.execute("SELECT to_regclass('factor.definition'), to_regclass('research.strategy')").fetchone()
        if ready[0] is None or ready[1] is None:
            raise RuntimeError("请先安装 D 和 C，再安装因子研究")
        with conn.transaction():
            conn.execute(MIGRATION.read_text(encoding="utf-8"))
        conn.commit()


if __name__ == "__main__":
    install()
    print("[OK] D3 installed")
