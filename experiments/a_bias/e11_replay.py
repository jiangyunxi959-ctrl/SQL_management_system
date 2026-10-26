"""E11 的 A 侧验证：本地财报修订后，用财务快照恢复选股和固定价格下的净值。

所有修改在同一测试事务中回滚。C 接入后还需保存策略、行情版本和成交参数。
"""

import csv
import pandas as pd
from run import connect, select_stocks, nav, HERE, ENTRY, END

if __name__ == "__main__":
    with connect() as conn, conn.cursor() as cur:
        try:
            original = select_stocks(cur)["E6"][0]
            cur.execute("SELECT clock_timestamp()")
            snapshot = cur.fetchone()[0]
            cur.execute(
                "UPDATE fin.report SET roe=0.07 WHERE stock_code='sh.600001' AND announce_date='2025-04-28'"
            )
            if cur.rowcount != 1:
                raise ValueError("需要 B+A 的合成样本库")
            changed = select_stocks(cur)["E6"][0]
            cur.execute(
                "SELECT set_config('app.data_snapshot_at',%s,true)",
                (snapshot.isoformat(),),
            )
            replay = select_stocks(cur)["E6"][0]
            assert original == replay and original != changed
            cur.execute(
                "SELECT trade_date,stock_code,close FROM mkt.v_bar_hfq WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date,stock_code",
                (ENTRY, END),
            )
            prices = pd.DataFrame(cur.fetchall(), columns=["date", "code", "close"])
            prices["close"] = prices["close"].astype(float)
            prices = prices.pivot(index="date", columns="code", values="close")
            curves = pd.DataFrame(
                {
                    "original": nav(prices, original),
                    "after_edit": nav(prices, changed),
                    "replay": nav(prices, replay),
                }
            )
            assert curves["original"].equals(curves["replay"])
            curves.to_csv(HERE / "E11_nav.csv", float_format="%.10f")
            with (HERE / "E11_selection.csv").open(
                "w", encoding="utf-8", newline=""
            ) as output:
                writer = csv.writer(output)
                writer.writerow(["stock_code", "original", "after_edit", "replay"])
                writer.writerows(
                    (code, code in original, code in changed, code in replay)
                    for code in sorted(set(original) | set(changed))
                )
            print(
                f"[PASS] E11: original={len(original)}, after_edit={len(changed)}, replay={len(replay)}; replay NAV exactly matches"
            )
        finally:
            conn.rollback()
