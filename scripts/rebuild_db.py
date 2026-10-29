from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg
from psycopg import sql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"missing environment variable: {name}")
    return value


def admin_conn(dbname: str):
    return psycopg.connect(
        host=env("PGADMIN_HOST", "127.0.0.1"),
        port=int(env("PGADMIN_PORT", "5432")),
        user=env("PGADMIN_USER", "postgres"),
        password=env("PGADMIN_PASSWORD"),
        dbname=dbname,
        autocommit=True,
    )


def execute_file(conn: psycopg.Connection, path: Path) -> None:
    print(f"[SQL] {path.relative_to(ROOT)}")
    text = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(text)


def set_role_passwords(conn: psycopg.Connection) -> None:
    mapping = {
        "etl_writer": os.getenv("ETL_WRITER_PASSWORD"),
        "app_user": os.getenv("APP_USER_PASSWORD"),
        "agent_reader": os.getenv("AGENT_READER_PASSWORD"),
        "auditor": os.getenv("AUDITOR_PASSWORD"),
        "audit_writer": os.getenv("AUDIT_WRITER_PASSWORD"),
        "auth_login": os.getenv("AUTH_LOGIN_PASSWORD"),
    }

    with conn.cursor() as cur:
        for role, password in mapping.items():
            if not password:
                continue

            cur.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role),
                    sql.Literal(password),
                )
            )
def ensure_d_roles(conn: psycopg.Connection) -> None:
    """CREATE ROLE 不能放进后续安装事务，所以在编号 SQL 之前准备。"""
    for role in ("audit_writer", "auth_login"):
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))


def sql_plan() -> list[Path]:
    sql_dir = ROOT / "sql"
    plan: list[Path] = [sql_dir / "00_init.sql"]

    ref_file = sql_dir / "10_ref.sql"
    if ref_file.exists():
        plan.append(ref_file)
        print("[INFO] using formal sql/10_ref.sql")
    else:
        plan.append(sql_dir / "dev" / "10_ref_stub.sql")
        print("[INFO] sql/10_ref.sql not found; using DEV-ONLY ref stub")

    # 其余正式 SQL 按数字前缀执行；20_mkt.sql 是今天 B 的主体。
    for prefix in ["20_", "30_", "40_", "50_", "60_", "70_", "80_", "90_"]:
        for p in sorted(sql_dir.glob(f"{prefix}*.sql")):
            if p not in plan:
                plan.append(p)

    plan.append(sql_dir / "seed" / "sample_data.sql")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the Zhishi development database from zero.")
    parser.add_argument("--yes", action="store_true", help="required confirmation for DROP DATABASE")
    parser.add_argument("--no-seed", action="store_true", help="build schema only; skip sample_data.sql")
    args = parser.parse_args()

    if not args.yes:
        raise SystemExit("Refusing to drop database without --yes")

    from app.setup_d import ensure_secrets
    ensure_secrets()

    dbname = env("PGDATABASE", "zhishi")
    if not dbname.replace("_", "").isalnum():
        raise SystemExit(f"unsafe database name: {dbname!r}")

    with admin_conn("postgres") as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW server_version_num")
            version_num = int(cur.fetchone()[0])
            major = version_num // 10000
            if major != 16:
                raise SystemExit(f"PostgreSQL 16 required by design, server is major {major}")

            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
                (dbname,),
            )
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(dbname)))
            cur.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(sql.Identifier(dbname)))

    with admin_conn(dbname) as conn:
        ensure_d_roles(conn)
        plan = sql_plan()
        for path in plan:
            if args.no_seed and path.name == "sample_data.sql":
                continue
            execute_file(conn, path)
            if path.name == "00_init.sql":
                set_role_passwords(conn)

    with admin_conn(dbname) as conn:
        from app.factors.catalog import register_public
        register_public(conn)
    from app.setup_ai import install as install_ai
    install_ai(dbname)
    from app.setup_d3 import install as install_d3
    install_d3(dbname)

    print(f"[OK] rebuilt database {dbname!r}")


if __name__ == "__main__":
    main()
