"""把完整的逐日成分股快照转为 load_memberships.py 接受的区间 CSV。

输入列：sample_date,index_code,index_name,stock_code,source_url。
每个指数、每个采样日必须给出完整名单；空名单用空 stock_code 的一行表示。
只合并连续自然日。周采样的中间六天没有证据，保持未知，不冒充精确调整日。
"""

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from etl.temporal.common import read_csv


def intervals(rows):
    groups = {}
    seen = set()
    for row in rows:
        day = date.fromisoformat(row["sample_date"])
        key = (row["index_code"], row["stock_code"], day)
        if key in seen:
            raise ValueError(f"重复快照行：{key}")
        seen.add(key)
        if not row["stock_code"]:
            continue
        group = (row["index_code"], row["stock_code"])
        groups.setdefault(group, []).append((day, row))
    output = []
    for (index, stock), samples in sorted(groups.items()):
        current = None
        for day, row in sorted(samples):
            # 来源也必须相同，避免把无法追溯的多份证据压成一个链接。
            if (
                current
                and current["end_date"] == day
                and current["source_url"] == row["source_url"]
            ):
                current["end_date"] = day + timedelta(days=1)
            else:
                current = dict(
                    stock_code=stock,
                    member_code=index,
                    member_name=row["index_name"],
                    start_date=day,
                    end_date=day + timedelta(days=1),
                    source_url=row["source_url"],
                )
                output.append(current)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = intervals(read_csv(args.input))
    fields = [
        "stock_code",
        "member_code",
        "member_name",
        "start_date",
        "end_date",
        "source_url",
    ]
    with args.output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(result)
    print(f"[PASS] 输出 {len(result)} 个有观察证据的成员区间")
