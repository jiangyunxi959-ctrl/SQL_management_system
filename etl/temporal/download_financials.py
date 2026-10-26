"""下载 BaoStock 季度盈利快照，转成 A 的财务导入格式。

用法：python etl/temporal/download_financials.py --year 2024 --quarter 4
当前接口不提供完整更正历史，因此 available_date 使用实际采集日。
历史 PIT 演示使用另行核对的公告版本，不把最新供应商值倒填到原公告日。
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import baostock as bs

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from etl.temporal.common import ROOT, read_csv
from etl.temporal.load_financials import FIELDS


def download(codes, year, quarter, path):
    observed = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    rows = []
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(login.error_msg)
    try:
        for code in codes:
            result = bs.query_profit_data(code=code, year=year, quarter=quarter)
            while result.error_code == "0" and result.next():
                raw = dict(zip(result.fields, result.get_row_data()))
                row = {field: "" for field in FIELDS}
                row.update(
                    stock_code=code,
                    report_period=raw["statDate"],
                    announce_date=raw["pubDate"],
                    available_date=observed,
                    observed_on=observed,
                    is_restated="false",
                    revenue=raw["MBRevenue"],
                    net_profit=raw["netProfit"],
                    eps=raw["epsTTM"],
                    roe=raw["roeAvg"],
                    source="BAOSTOCK",
                    source_url="https://github.com/zxygithub/baostock/blob/master/docs/pythonAPI.md",
                    source_kind="PROVIDER_SNAPSHOT",
                    period_basis="ANNUAL" if quarter == 4 else "YTD",
                    profit_basis="CONSOLIDATED",
                    eps_basis="TTM",
                    notes="供应商盈利快照；revenue 为主营营业收入，net_profit 为合并净利润；ROE 原值按接口小数示例保留，历史使用前须对照公告。",
                )
                rows.append(row)
            if result.error_code != "0":
                raise RuntimeError(f"{code}: {result.error_msg}")
    finally:
        bs.logout()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS + ["observed_on"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"[PASS] 下载 {len(rows)} 条快照到 {path}；可用日起点 {observed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--quarter", type=int, choices=[1, 2, 3, 4], required=True)
    parser.add_argument("--codes", nargs="+")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/raw/baostock/financials_a.csv"
    )
    args = parser.parse_args()
    codes = args.codes or [
        r["code"] for r in read_csv(ROOT / "data/raw/baostock/stock_basic.csv")
    ]
    download(codes, args.year, args.quarter, args.output)
