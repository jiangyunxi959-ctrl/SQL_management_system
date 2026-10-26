"""把 A 接入已建好的 B 数据库：一个事务执行，不删库、不改行情。

合成样例另运行 setup_a_sample.py。执行前按团队惯例保留数据库备份。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl.temporal.common import ROOT, connect

if __name__ == "__main__":
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass('mkt.daily_bar'),to_regclass('mkt.mv_indicator_daily')"
        )
        if not all(cur.fetchone()):
            raise ValueError("请先按 B 的说明建立行情模块")
        for filename in [
            "10_ref.sql",
            "30_fin.sql",
            "40_pit.sql",
            "seed/reference_rules.sql",
        ]:
            cur.execute((ROOT / "sql" / filename).read_text(encoding="utf-8"))
            print(f"[SQL] {filename}")
    print("[PASS] A 已接入；原行情保留")
