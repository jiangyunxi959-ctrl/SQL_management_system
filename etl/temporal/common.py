"""A 的小工具：连接数据库、读取 CSV。无 ORM，所有 SQL 都能直接看见。"""

import csv
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def connect():
    return psycopg.connect(
        host=os.getenv("PGADMIN_HOST", "127.0.0.1"),
        port=int(os.getenv("PGADMIN_PORT", "5432")),
        user=os.getenv("PGADMIN_USER", "postgres"),
        password=os.environ["PGADMIN_PASSWORD"],
        dbname=os.getenv("PGDATABASE", "zhishi"),
    )


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def optional(value):
    return (
        None
        if value is None or value.strip() in ("", "NULL", "None")
        else value.strip()
    )
