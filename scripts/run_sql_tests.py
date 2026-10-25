from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"missing environment variable: {name}")
    return value


def connect():
    return psycopg.connect(
        host=env("PGADMIN_HOST", "127.0.0.1"),
        port=int(env("PGADMIN_PORT", "5432")),
        user=env("PGADMIN_USER", "postgres"),
        password=env("PGADMIN_PASSWORD"),
        dbname=env("PGDATABASE", "zhishi"),
    )


def run_sql_tests() -> None:
    tests = sorted((ROOT / "sql" / "tests").glob("test_*.sql"))
    if not tests:
        raise RuntimeError("no SQL tests found")

    for path in tests:
        with connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(path.read_text(encoding="utf-8"))
                # 测试允许写临时批次，但不污染样本库。
                conn.rollback()
                print(f"[PASS] {path.name}")
            except Exception:
                conn.rollback()
                print(f"[FAIL] {path.name}")
                raise


def test_ema_against_pandas() -> None:
    query = """
        SELECT trade_date, close_hfq::double precision, ema12::double precision, ema26::double precision
        FROM mkt.mv_indicator_daily
        WHERE stock_code = 'sh.600001'
        ORDER BY trade_date
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

    df = pd.DataFrame(rows, columns=["trade_date", "close", "ema12_sql", "ema26_sql"])
    if df.empty:
        raise AssertionError("EMA test has no rows")

    df["ema12_pd"] = df["close"].ewm(span=12, adjust=False).mean()
    df["ema26_pd"] = df["close"].ewm(span=26, adjust=False).mean()
    err12 = (df["ema12_sql"] - df["ema12_pd"]).abs().max()
    err26 = (df["ema26_sql"] - df["ema26_pd"]).abs().max()
    if err12 >= 1e-6 or err26 >= 1e-6:
        raise AssertionError(f"EMA mismatch: ema12={err12}, ema26={err26}")
    print(f"[PASS] EMA vs pandas (max error: ema12={err12:.3g}, ema26={err26:.3g})")


def print_summary() -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM mkt.daily_bar")
            bars = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM mkt.mv_indicator_daily")
            indicators = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM etl.dq_missing_bar")
            missing = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM etl.dq_bar_on_closed_day")
            closed = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM etl.dq_bar_without_factor")
            nofactor = cur.fetchone()[0]
    print(f"[SUMMARY] daily_bar={bars}, indicators={indicators}, DQ=({missing}, {closed}, {nofactor})")


if __name__ == "__main__":
    run_sql_tests()
    test_ema_against_pandas()
    print_summary()
    print("[ALL PASS] B 10/25 market + indicator acceptance passed")
