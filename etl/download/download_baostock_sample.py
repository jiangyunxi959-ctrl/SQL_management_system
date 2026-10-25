from __future__ import annotations

import argparse
import time
from pathlib import Path

import baostock as bs
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "raw" / "baostock"
DAILY_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,"
    "tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST"
)


def result_to_df(rs) -> pd.DataFrame:
    rows: list[list[str]] = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(f"BaoStock error {rs.error_code}: {rs.error_msg}")
    return pd.DataFrame(rows, columns=rs.fields)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a small real BaoStock dataset for field verification.")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--stocks", type=int, default=20)
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    login = bs.login()
    if login.error_code != "0":
        raise SystemExit(f"BaoStock login failed {login.error_code}: {login.error_msg}")

    try:
        basic = result_to_df(bs.query_stock_basic())
        basic = basic[
            (basic["type"] == "1")
            & (basic["status"] == "1")
            & basic["code"].str.match(r"^(sh|sz)\.")
            & basic["ipoDate"].str.match(r"^\d{4}-\d{2}-\d{2}$")
            & (basic["ipoDate"] <= args.start)
        ].sort_values("code").head(args.stocks).copy()
        if len(basic) < args.stocks:
            raise RuntimeError(f"only selected {len(basic)} stocks, need {args.stocks}")

        trade_dates = result_to_df(bs.query_trade_dates(start_date=args.start, end_date=args.end))

        daily_frames: list[pd.DataFrame] = []
        factor_frames: list[pd.DataFrame] = []
        for _, row in basic.iterrows():
            code = row["code"]
            print(f"[DOWNLOAD] {code}")
            k = result_to_df(
                bs.query_history_k_data_plus(
                    code,
                    DAILY_FIELDS,
                    start_date=args.start,
                    end_date=args.end,
                    frequency="d",
                    adjustflag="3",  # 原始不复权行情
                )
            )
            daily_frames.append(k)

            # 从上市日开始取全部因子，避免只截取样本期而丢失此前累计因子。
            f = result_to_df(
                bs.query_adjust_factor(
                    code=code,
                    start_date=row["ipoDate"],
                    end_date=args.end,
                )
            )
            if not f.empty:
                factor_frames.append(f)
            time.sleep(0.03)

        daily = pd.concat(daily_frames, ignore_index=True)
        factors = (
            pd.concat(factor_frames, ignore_index=True)
            if factor_frames
            else pd.DataFrame(columns=["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"])
        )

        basic.to_csv(OUT / "stock_basic.csv", index=False, encoding="utf-8-sig")
        trade_dates.to_csv(OUT / "trade_dates.csv", index=False, encoding="utf-8-sig")
        daily.to_csv(OUT / "daily_bar.csv", index=False, encoding="utf-8-sig")
        factors.to_csv(OUT / "adjust_factor.csv", index=False, encoding="utf-8-sig")
        (OUT / "selected_codes.txt").write_text("\n".join(basic["code"]) + "\n", encoding="utf-8")

        print(f"[OK] stock_basic={len(basic)}, trade_dates={len(trade_dates)}, daily_bar={len(daily)}, factors={len(factors)}")
        print(f"[OK] raw files -> {OUT}")
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
