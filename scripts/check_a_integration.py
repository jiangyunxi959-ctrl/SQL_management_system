"""在自动创建的临时数据库里验证 A 的 CSV 接入；结束后仅删除本脚本创建的库。

需要管理员建库权限。原 PGDATABASE 不改动，B 的文件也不修改。
"""

import csv
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import date
from pathlib import Path

from psycopg import sql
from psycopg.errors import ForeignKeyViolation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.rebuild_db import admin_conn, execute_file
from etl.temporal.common import connect, read_csv
from etl.temporal.load_financials import load as load_financials
from etl.temporal.load_reference import load_base, load_status
from etl.temporal.load_memberships import load as load_memberships
from etl.load import load_daily_bar


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def check_reference(folder):
    write_csv(
        folder / "stock_basic.csv",
        [
            dict(
                code="sh.600000",
                code_name="观察日名称",
                ipoDate="1999-11-10",
                outDate="",
            )
        ],
    )
    days = ["2025-03-07", "2025-03-08", "2025-03-09", "2025-03-10", "2025-03-11"]
    write_csv(
        folder / "trade_dates.csv",
        [
            dict(calendar_date=day, is_trading_day="0" if day in days[1:3] else "1")
            for day in days
        ],
    )
    load_base(folder, date(2025, 3, 11))
    load_base(folder, date(2025, 3, 11))
    rows = []
    for day, flag in [("2025-03-07", "0"), ("2025-03-10", "1"), ("2025-03-11", "1")]:
        row = {field: "" for field in load_daily_bar.FIELDS}
        row.update(
            date=day,
            code="sh.600000",
            open="10",
            high="11",
            low="9",
            close="10",
            preclose="10",
            volume="1000",
            amount="10000",
            adjustflag="3",
            tradestatus="1",
            isST=flag,
        )
        rows.append(row)
    path = folder / "daily_bar.csv"
    write_csv(path, rows)
    load_daily_bar.CSV_PATH = path
    load_daily_bar.main()
    # 已有行情后再接入一次 A，验证重跑接入脚本不改 B 的价格。
    subprocess.run([sys.executable, str(ROOT / "scripts/apply_a.py")], check=True)
    load_status()
    load_status()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*),sum(close) FROM mkt.daily_bar")
        assert cur.fetchone() == (3, 30)
        cur.execute(
            "SELECT status,lower(valid),upper(valid) FROM ref.security_status ORDER BY lower(valid)"
        )
        assert cur.fetchall() == [
            ("NORMAL", date(2025, 3, 7), date(2025, 3, 10)),
            ("ST", date(2025, 3, 10), date(2025, 3, 12)),
        ]
        cur.execute("SELECT set_config('app.as_of_date','2025-03-10',true)")
        cur.execute("SELECT name FROM pit.security WHERE stock_code='sh.600000'")
        assert cur.fetchone()[0] is None, "未来名称被倒填"
    member = dict(
        stock_code="sh.600000",
        member_code="TEST",
        member_name="测试指数",
        start_date="2025-03-07",
        end_date="2025-03-12",
        source_url="test://fixture",
    )
    path = folder / "members.csv"
    write_csv(path, [member])
    load_memberships(path, "index")
    load_memberships(path, "index")
    print("[PASS] B CSV → A 证券/日历 → B 行情 → A ST 区间；名称截止、成员幂等")


def check_financials(folder):
    subprocess.run(
        [sys.executable, str(ROOT / "etl/temporal/load_restatement_examples.py")],
        check=True,
    )
    original = ROOT / "etl/temporal/restatement_examples.csv"
    assert load_financials(original) == 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM fin.report_history")
        assert cur.fetchone()[0] == 0, "重复导入不应制造系统版本"
        for as_of, expected in [
            ("2024-04-26", "108049291.94"),
            ("2024-04-27", "78967390.19"),
        ]:
            cur.execute("SELECT set_config('app.as_of_date',%s,true)", (as_of,))
            cur.execute("SELECT net_profit FROM pit.fn_fundamentals('sh.603595',1)")
            assert str(cur.fetchone()[0]) == expected
    rows = read_csv(original)
    snapshot = dict(
        rows[0],
        source_kind="PROVIDER_SNAPSHOT",
        available_date="2026-10-26",
        observed_on="2026-10-26",
    )
    path = folder / "snapshot_conflict.csv"
    write_csv(path, [snapshot])
    try:
        load_financials(path)
        raise AssertionError("供应商快照覆盖了已核对公告")
    except ValueError:
        pass
    # 第一行修改本可成功，第二行外键错误；整个文件必须回滚。
    rows[0]["net_profit"] = "123"
    rows[1]["stock_code"] = "sh.699999"
    path = folder / "bad.csv"
    write_csv(path, rows[:2])
    try:
        load_financials(path)
        raise AssertionError("错误文件未被拒绝")
    except ForeignKeyViolation:
        pass
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT net_profit FROM fin.report WHERE stock_code='sz.300730' AND announce_date='2023-08-19'"
        )
        assert str(cur.fetchone()[0]) == "-17770558.17"
        cur.execute("SELECT count(*) FROM staging.financial_raw")
        assert cur.fetchone()[0] == 6
        cur.execute("SELECT count(*) FROM fin.report_history")
        assert cur.fetchone()[0] == 0
    print(
        "[PASS] 三组真实更正、重复导入零变化、来源冲突拒绝、错误文件整批回滚（包括 staging 和 history）"
    )


if __name__ == "__main__":
    name = "zhishi_a_io_" + uuid.uuid4().hex[:12]
    original_db = os.environ.get("PGDATABASE")
    with admin_conn("postgres") as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            os.environ["PGDATABASE"] = name
            with admin_conn(name) as conn:
                for filename in ["00_init.sql", "dev/10_ref_stub.sql", "20_mkt.sql"]:
                    execute_file(conn, ROOT / "sql" / filename)
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/apply_a.py")], check=True
            )
            with tempfile.TemporaryDirectory(prefix="zhishi_a_") as temp:
                check_reference(Path(temp))
                check_financials(Path(temp))
        finally:
            if original_db is None:
                os.environ.pop("PGDATABASE", None)
            else:
                os.environ["PGDATABASE"] = original_db
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
    print("[ALL PASS] A CSV integration; temporary database removed")
