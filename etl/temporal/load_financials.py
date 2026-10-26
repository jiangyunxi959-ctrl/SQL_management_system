"""把人工核对或供应商采集的财务 CSV 入库。先完整校验，再在一个事务内写入。

金额统一人民币元，ROE 统一小数；缺失值留空，绝不写成 0。
供应商最新快照 available_date 必须是采集日；DISCLOSURE 必须保留原公告链接。
"""

import argparse
import sys
import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from etl.temporal.common import connect, read_csv, optional

FIELDS = [
    "stock_code",
    "report_period",
    "announce_date",
    "available_date",
    "is_restated",
    "revenue",
    "net_profit",
    "total_assets",
    "total_equity",
    "eps",
    "roe",
    "source",
    "source_url",
    "source_kind",
    "period_basis",
    "profit_basis",
    "eps_basis",
    "notes",
]
NUMBERS = ["revenue", "net_profit", "total_assets", "total_equity", "eps", "roe"]


def validate(row):
    result = dict(row)
    for field in ("report_period", "announce_date", "available_date"):
        result[field] = date.fromisoformat(row[field])
    if (
        result["announce_date"] <= result["report_period"]
        or result["available_date"] < result["announce_date"]
    ):
        raise ValueError("公告日必须晚于报告期，可用日不得早于公告日")
    if row["source_kind"] == "PROVIDER_SNAPSHOT":
        if not row.get("observed_on") or result["available_date"] != date.fromisoformat(
            row["observed_on"]
        ):
            raise ValueError(
                "供应商快照须提供 observed_on，且 available_date 等于采集日"
            )
    if row["source_kind"] == "DISCLOSURE" and not row["source_url"].startswith(
        ("https://", "http://")
    ):
        raise ValueError("真实披露需要公告链接")
    if row["is_restated"].lower() not in ("true", "false", "1", "0"):
        raise ValueError("is_restated 必须为 true/false")
    result["is_restated"] = row["is_restated"].lower() in ("true", "1")
    for field in NUMBERS:
        value = optional(row.get(field))
        result[field] = Decimal(value) if value else None
        if result[field] is not None and not result[field].is_finite():
            raise ValueError(f"{field} 必须是有限数值")
    result["eps_basis"] = row.get("eps_basis") or "BASIC"
    result["notes"] = row.get("notes") or ""
    return result


def load(path):
    rows = []
    seen = set()
    originals = read_csv(path)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    for line, row in enumerate(originals, 2):
        try:
            parsed = validate(row)
            key = tuple(parsed[f] for f in FIELDS[:3])
            if key in seen:
                raise ValueError("同一批 CSV 出现重复披露键")
            seen.add(key)
            rows.append(parsed)
        except (ValueError, KeyError, InvalidOperation) as error:
            raise ValueError(f"CSV 第 {line} 行：{error}") from error
    changed = 0
    # 列名来自固定 FIELDS，CSV 内容始终通过 %s 参数传入。
    columns = ",".join(FIELDS)
    placeholders = ",".join(["%s"] * len(FIELDS))
    updates = ",".join(f"{f}=EXCLUDED.{f}" for f in FIELDS[3:])
    old = ",".join(f"old.{f}" for f in FIELDS[3:])
    new = ",".join(f"EXCLUDED.{f}" for f in FIELDS[3:])
    query = f"""INSERT INTO fin.report AS old ({columns}) VALUES ({placeholders})
        ON CONFLICT(stock_code,report_period,announce_date) DO UPDATE SET {updates}
        WHERE ({old}) IS DISTINCT FROM ({new})"""
    with connect() as conn, conn.cursor() as cur:
        for line, (raw, row) in enumerate(zip(originals, rows), 2):
            cur.execute(
                """SELECT source_kind,available_date FROM fin.report
                WHERE stock_code=%s AND report_period=%s AND announce_date=%s""",
                (row["stock_code"], row["report_period"], row["announce_date"]),
            )
            previous = cur.fetchone()
            if previous and previous[0] != row["source_kind"]:
                raise ValueError(
                    "同一披露已有其他来源类型，不能用供应商快照覆盖已核对公告"
                )
            if (
                previous
                and row["source_kind"] == "PROVIDER_SNAPSHOT"
                and previous[1] != row["available_date"]
            ):
                raise ValueError(
                    "同一披露已保存其他采集日的快照；请保留原文件并核对版本，不覆盖旧可用日期"
                )
            cur.execute(
                """INSERT INTO staging.financial_raw(source_file,content_hash,line_no,payload)
                VALUES (%s,%s,%s,%s) ON CONFLICT(content_hash,line_no) DO NOTHING""",
                (path.name, content_hash, line, Jsonb(raw)),
            )
            cur.execute(query, [row[f] for f in FIELDS])
            changed += cur.rowcount
    print(f"[PASS] 财务入库：输入 {len(rows)}，实际变化 {changed}")
    return changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    load(parser.parse_args().csv)
