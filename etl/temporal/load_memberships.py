"""导入人工核对的历史指数成员/行业成员区间。

CSV: stock_code,member_code,member_name,start_date,end_date,source_url
end_date 留空表示尚无已知结束日；来源须说明边界依据。
重叠但不相同的区间直接拒绝，避免静默覆盖历史。
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from etl.temporal.common import connect, read_csv, optional


def load(path, kind):
    rows = read_csv(path)
    changed = 0
    with connect() as conn, conn.cursor() as cur:
        for row in rows:
            start = date.fromisoformat(row["start_date"])
            end = (
                date.fromisoformat(row["end_date"])
                if optional(row.get("end_date"))
                else None
            )
            if end is not None and end <= start:
                raise ValueError("区间结束日必须晚于开始日")
            if not optional(row.get("source_url")):
                raise ValueError("每个区间需要来源")
            if kind == "index":
                cur.execute(
                    "INSERT INTO ref.index_info VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (row["member_code"], row["member_name"]),
                )
                cur.execute(
                    """INSERT INTO ref.index_member VALUES (%s,%s,daterange(%s,%s,'[)'),%s)
                            ON CONFLICT(index_code,stock_code,valid) DO NOTHING""",
                    (
                        row["member_code"],
                        row["stock_code"],
                        start,
                        end,
                        row["source_url"],
                    ),
                )
            else:
                cur.execute(
                    """SELECT industry_code,source FROM ref.industry_member
                    WHERE stock_code=%s AND valid=daterange(%s,%s,'[)')""",
                    (row["stock_code"], start, end),
                )
                previous = cur.fetchone()
                if previous and previous != (row["member_code"], row["source_url"]):
                    raise ValueError("同一区间的行业或来源冲突，请核对后显式更正")
                cur.execute(
                    "INSERT INTO ref.industry(industry_code,name) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (row["member_code"], row["member_name"]),
                )
                cur.execute(
                    """INSERT INTO ref.industry_member VALUES (%s,%s,daterange(%s,%s,'[)'),%s)
                            ON CONFLICT(stock_code,valid) DO NOTHING""",
                    (
                        row["stock_code"],
                        row["member_code"],
                        start,
                        end,
                        row["source_url"],
                    ),
                )
            changed += cur.rowcount
    print(f"[PASS] {kind} 成员区间输入 {len(rows)}，新增 {changed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--kind", choices=["index", "industry"], required=True)
    args = parser.parse_args()
    load(args.csv, args.kind)
