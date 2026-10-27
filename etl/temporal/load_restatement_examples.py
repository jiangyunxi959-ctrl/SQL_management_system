"""导入三组真实报告期的更正案例（两家公司），用于独立财报演示库。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from etl.temporal.common import connect
from etl.temporal.load_financials import load

if __name__ == "__main__":
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT EXISTS(SELECT 1 FROM etl.load_batch WHERE source='SAMPLE')")
        if cur.fetchone()[0]:
            raise ValueError("请在 --no-seed 的独立案例库导入，避免与 B 的合成证券混合")
        # 上市公告书：3910507（科创信息）、3551966（东尼电子），链接见 docs/A_真实更正案例.md。
        cur.execute("""INSERT INTO ref.security VALUES
            ('sz.300730','SZ','GEM','2017-12-05',NULL),
            ('sh.603595','SH','MAIN','2017-07-12',NULL) ON CONFLICT DO NOTHING""")
    load(Path(__file__).with_name("restatement_examples.csv"))
