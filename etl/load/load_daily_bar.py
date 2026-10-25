from __future__ import annotations

import csv
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
CSV_PATH = ROOT / "data" / "raw" / "baostock" / "daily_bar.csv"
FIELDS = [
    "date", "code", "open", "high", "low", "close", "preclose", "volume", "amount",
    "adjustflag", "turn", "tradestatus", "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM", "isST",
]


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


def main() -> None:
    if not CSV_PATH.exists():
        raise SystemExit(f"missing {CSV_PATH}; run download_baostock_sample.py first")

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    missing_cols = [x for x in FIELDS if x not in (reader.fieldnames or [])]
    if missing_cols:
        raise SystemExit(f"CSV missing columns: {missing_cols}")

    codes = sorted({r["code"] for r in rows})
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT stock_code FROM ref.security WHERE stock_code = ANY(%s)", (codes,))
            existing = {r[0] for r in cur.fetchall()}
            missing = [c for c in codes if c not in existing]
            if missing:
                raise SystemExit(
                    "ref.security 尚缺这些证券：" + ", ".join(missing[:10]) +
                    (" ..." if len(missing) > 10 else "") +
                    "。正式协作中由 A 的 ref ETL 先导入；不要在 B 的 loader 里越权创建 ref 表。"
                )

            cur.execute(
                "INSERT INTO etl.load_batch(source,target,params) VALUES ('BAOSTOCK','mkt.daily_bar',%s::jsonb) RETURNING batch_id",
                ('{"file":"daily_bar.csv"}',),
            )
            batch_id = cur.fetchone()[0]

            with cur.copy(
                "COPY staging.daily_bar_raw(batch_id,date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST) FROM STDIN"
            ) as copy:
                for r in rows:
                    copy.write_row([batch_id] + [r.get(k, "") for k in FIELDS])

            cur.execute("SELECT etl.fn_merge_daily_bar(%s)", (batch_id,))
            changed = cur.fetchone()[0]
            cur.execute("SELECT rows_in, rows_upsert, rows_reject FROM etl.load_batch WHERE batch_id=%s", (batch_id,))
            metrics = cur.fetchone()
        conn.commit()

    print(f"[OK] batch={batch_id}, changed={changed}, rows_in/upsert/reject={metrics}")


if __name__ == "__main__":
    main()
