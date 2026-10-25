"""Load BaoStock reference CSVs into the isolated zhishi_real DEV database.

This is a temporary B-side fixture. A owns the production ref schema and ETL.
Run from the project root: python etl/load/load_dev_ref.py
"""
from __future__ import annotations

import csv
import os
import re
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "baostock"
load_dotenv(ROOT / ".env")


def read_csv(filename: str) -> list[dict[str, str]]:
    with (RAW / filename).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def get_board(code: str) -> tuple[str, str]:
    if not re.fullmatch(r"(?:sh|sz|bj)\.\d{6}", code):
        raise ValueError(f"Unexpected stock code: {code}")
    if code.startswith("sh."):
        return "SH", "STAR" if code.startswith("sh.688") else "MAIN"
    if code.startswith("sz."):
        return "SZ", "GEM" if code.startswith("sz.30") else "MAIN"
    return "BJ", "BSE"


def optional_date(value: str) -> date | None:
    return date.fromisoformat(value) if value and value.upper() != "NULL" else None


def main() -> None:
    basic = read_csv("stock_basic.csv")
    calendar = read_csv("trade_dates.csv")
    if len({r["code"] for r in basic}) != len(basic):
        raise ValueError("Duplicate stock codes in stock_basic.csv")
    if len({r["calendar_date"] for r in calendar}) != len(calendar):
        raise ValueError("Duplicate dates in trade_dates.csv")

    securities = []
    for r in basic:
        exchange, board = get_board(r["code"])
        securities.append((
            r["code"], exchange, board, date.fromisoformat(r["ipoDate"]),
            optional_date(r["outDate"]),
        ))

    dates = []
    for r in calendar:
        if r["is_trading_day"] not in ("0", "1"):
            raise ValueError(f"Unexpected calendar flag: {r}")
        dates.append((date.fromisoformat(r["calendar_date"]), r["is_trading_day"] == "1"))

    # Intentionally hardcoded: NEVER load the production/sample 'zhishi' database.
    with psycopg.connect(
        host=os.getenv("PGADMIN_HOST", "127.0.0.1"),
        port=int(os.getenv("PGADMIN_PORT", "5432")),
        user=os.getenv("PGADMIN_USER", "postgres"),
        password=os.environ["PGADMIN_PASSWORD"],
        dbname="zhishi_real",
    ) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO ref.security(stock_code, exchange, board, list_date, delist_date)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (stock_code) DO UPDATE SET
                     exchange=EXCLUDED.exchange, board=EXCLUDED.board,
                     list_date=EXCLUDED.list_date, delist_date=EXCLUDED.delist_date""",
                securities,
            )
            cur.executemany(
                """INSERT INTO ref.trading_calendar(cal_date, is_open)
                   VALUES (%s, %s)
                   ON CONFLICT (cal_date) DO UPDATE SET is_open=EXCLUDED.is_open""",
                dates,
            )
            cur.execute("SELECT COUNT(*) FROM ref.security")
            n_securities = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE is_open) FROM ref.trading_calendar")
            n_calendar, n_open = cur.fetchone()

    print("[OK] reference fixtures loaded into zhishi_real")
    print(f"[CHECK] securities={n_securities}, calendar_days={n_calendar}, open_days={n_open}")
    print("[NOTE] DEV-ONLY reference data; A owns the formal ref ETL")


if __name__ == "__main__":
    main()
