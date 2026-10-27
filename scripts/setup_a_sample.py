"""在已经重建好的 B 合成库中补 A 案例；不删库，不重新计算 B 指标。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl.temporal.common import ROOT, connect

if __name__ == "__main__":
    with connect() as conn, conn.cursor() as cur:
        cur.execute((ROOT / "sql/seed/reference_rules.sql").read_text(encoding="utf-8"))
        cur.execute((ROOT / "sql/seed/pit_fixtures.sql").read_text(encoding="utf-8"))
    print("[PASS] A 的合成案例已载入，可重复执行")
