"""正式接入 B 的股票与日历 CSV，并把已校验日线的 ST 标记整理成区间。

例：python etl/temporal/load_reference.py --observed-on 2026-10-26
先导入 reference，再运行 B 的行情 loader，最后加 --status-only 整理状态。
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from etl.temporal.common import ROOT, connect, read_csv, optional


def board_of(code):
    if not re.fullmatch(r"(sh|sz|bj)\.[0-9]{6}", code):
        raise ValueError(f"股票代码格式不正确：{code}")
    if code.startswith("sh."):
        return "SH", "STAR" if code.startswith("sh.688") else "MAIN"
    if code.startswith("sz."):
        return "SZ", "GEM" if code.startswith(("sz.300", "sz.301")) else "MAIN"
    return "BJ", "BSE"


def load_base(raw_dir, observed_on):
    stocks = read_csv(raw_dir / "stock_basic.csv")
    days = read_csv(raw_dir / "trade_dates.csv")
    if len({r["code"] for r in stocks}) != len(stocks):
        raise ValueError("股票 CSV 有重复代码")
    if len({r["calendar_date"] for r in days}) != len(days):
        raise ValueError("日历 CSV 有重复日期")
    changed = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT EXISTS(SELECT 1 FROM etl.load_batch WHERE source='SAMPLE')")
        if cur.fetchone()[0]:
            raise ValueError("真实参考数据请使用独立数据库；当前库包含 B 的合成样本")
        for row in stocks:
            exchange, board = board_of(row["code"])
            listed = date.fromisoformat(row["ipoDate"])
            cur.execute(
                """INSERT INTO ref.security AS old VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT(stock_code) DO UPDATE SET exchange=EXCLUDED.exchange,
                board=EXCLUDED.board,list_date=EXCLUDED.list_date,delist_date=EXCLUDED.delist_date
                WHERE (old.exchange,old.board,old.list_date,old.delist_date) IS DISTINCT FROM
                      (EXCLUDED.exchange,EXCLUDED.board,EXCLUDED.list_date,EXCLUDED.delist_date)""",
                (row["code"], exchange, board, listed, optional(row.get("outDate"))),
            )
            changed += cur.rowcount
            # 基本资料是本次观察快照；不把现名假装成上市以来的历史简称。
            cur.execute(
                """SELECT name,lower(valid) FROM ref.security_name
                           WHERE stock_code=%s AND valid @> %s::date""",
                (row["code"], observed_on),
            )
            previous = cur.fetchone()
            if previous and previous[0] == row["code_name"]:
                continue
            if previous and previous[1] >= observed_on:
                raise ValueError("同日名称冲突，请核对来源后更正")
            if previous:
                cur.execute(
                    """UPDATE ref.security_name SET valid=daterange(lower(valid),%s,'[)')
                               WHERE stock_code=%s AND valid @> %s::date""",
                    (observed_on, row["code"], observed_on),
                )
            cur.execute(
                """INSERT INTO ref.security_name VALUES (%s,%s,daterange(%s,NULL,'[)'),%s)""",
                (
                    row["code"],
                    row["code_name"],
                    max(listed, observed_on),
                    "BAOSTOCK-SNAPSHOT",
                ),
            )
        for row in days:
            if row["is_trading_day"] not in ("0", "1"):
                raise ValueError("交易日标记必须是 0 或 1")
            cur.execute(
                """INSERT INTO ref.trading_calendar AS old VALUES (%s,%s)
                ON CONFLICT(cal_date) DO UPDATE SET is_open=EXCLUDED.is_open
                WHERE old.is_open IS DISTINCT FROM EXCLUDED.is_open""",
                (
                    date.fromisoformat(row["calendar_date"]),
                    row["is_trading_day"] == "1",
                ),
            )
            changed += cur.rowcount
    print(f"[PASS] 正式证券和日历导入完成；实际变化 {changed} 行")


def status_intervals(rows):
    """输入按日期排序的 (日期, ST标记)，输出 [开始,结束)；断档由调用方先检查。"""
    result = []
    for day, flag in rows:
        status = "ST" if flag == "1" else "NORMAL"
        if not result or result[-1][0] != status:
            if result:
                result[-1][2] = day
            result.append([status, day, None])
    if result:
        from datetime import timedelta

        result[-1][2] = rows[-1][0] + timedelta(days=1)
    return result


def load_status():
    from collections import defaultdict

    with connect() as conn, conn.cursor() as cur:
        # 只消费已经被 B 接受的行，避免原始表中的拒绝行进入正式状态。
        cur.execute("""SELECT DISTINCT ON(r.code,r.date) r.code,r.date::date,r.isST
            FROM staging.daily_bar_raw r JOIN mkt.daily_bar b
              ON b.stock_code=r.code AND b.trade_date=etl.try_date(r.date)
            WHERE r.isST IN ('0','1') AND NOT EXISTS (
              SELECT 1 FROM etl.reject_row x WHERE x.batch_id=r.batch_id AND x.row_key=r.code||'/'||r.date)
            ORDER BY r.code,r.date,r.batch_id DESC,r.raw_id DESC""")
        by_code = defaultdict(list)
        for code, day, flag in cur.fetchall():
            by_code[code].append((day, flag))
        changed = 0
        for code, rows in by_code.items():
            cur.execute(
                """SELECT cal_date FROM ref.trading_calendar
                           WHERE is_open AND cal_date BETWEEN %s AND %s ORDER BY cal_date""",
                (rows[0][0], rows[-1][0]),
            )
            if [r[0] for r in cur.fetchall()] != [r[0] for r in rows]:
                raise ValueError(f"{code} 的 ST 观察有交易日缺口，请补齐再转换")
            wanted = status_intervals(rows)
            cur.execute(
                """SELECT status,lower(valid),upper(valid) FROM ref.security_status
                           WHERE stock_code=%s AND source='BAOSTOCK-DAILY' ORDER BY lower(valid)""",
                (code,),
            )
            if [list(r) for r in cur.fetchall()] == wanted:
                continue
            cur.execute(
                "DELETE FROM ref.security_status WHERE stock_code=%s AND source='BAOSTOCK-DAILY'",
                (code,),
            )
            for status, start, end in wanted:
                cur.execute(
                    """INSERT INTO ref.security_status VALUES (%s,%s,daterange(%s,%s,'[)'),%s)""",
                    (code, status, start, end, "BAOSTOCK-DAILY"),
                )
                changed += 1
    print(f"[PASS] ST 区间导入完成；写入 {changed} 段")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw/baostock")
    parser.add_argument("--observed-on", type=date.fromisoformat, default=date.today())
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()
    if args.status_only:
        load_status()
    else:
        load_base(args.raw_dir, args.observed_on)
