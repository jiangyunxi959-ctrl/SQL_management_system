from __future__ import annotations

import csv
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
CSV_PATH = ROOT / "data" / "raw" / "baostock" / "adjust_factor.csv"
FIELDS = ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"]


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

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO etl.load_batch(source,target,params) VALUES ('BAOSTOCK','mkt.adj_factor',%s::jsonb) RETURNING batch_id",
                ('{"file":"adjust_factor.csv"}',),
            )
            batch_id = cur.fetchone()[0]

            if rows:
                with cur.copy(
                    'COPY staging.adjust_factor_raw(batch_id,code,"dividOperateDate","foreAdjustFactor","backAdjustFactor","adjustFactor") FROM STDIN'
                ) as copy:
                    for r in rows:
                        copy.write_row([batch_id] + [r.get(k, "") for k in FIELDS])

            cur.execute("SELECT etl.fn_merge_adj_factor(%s)", (batch_id,))
            changed = cur.fetchone()[0]
            cur.execute("SELECT rows_in, rows_upsert, rows_reject FROM etl.load_batch WHERE batch_id=%s", (batch_id,))
            metrics = cur.fetchone()
        conn.commit()

    print(f"[OK] batch={batch_id}, changed={changed}, rows_in/upsert/reject={metrics}")
    print("[NEXT] REFRESH MATERIALIZED VIEW mkt.mv_indicator_daily; after both bars and factors are loaded")


if __name__ == "__main__":
    main()
