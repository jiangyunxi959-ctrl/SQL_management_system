"""独立验证 zhishi_real 中全部真实股票的 SQL 技术指标（只读）。

运行：在 zhishi 项目根目录执行
    python scripts/verify_real_indicators.py

不会重建数据库、刷新物化视图或修改任何数据。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DB_NAME = "zhishi_real"  # 固定连接真实数据测试库，绝不使用默认 zhishi

FIELDS = {
    "close_hfq": (1e-8, 1e-10),
    "ma20": (1e-8, 1e-10),
    "ma60": (1e-8, 1e-10),
    "ema12": (1e-6, 1e-10),
    "ema26": (1e-6, 1e-10),
    "ret_20d": (1e-8, 1e-9),
    "ret_60d": (1e-8, 1e-9),
    "vol_20d": (1e-8, 1e-9),
    "amount_20d": (1e-4, 1e-10),
}


def read_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    with psycopg.connect(
        host=os.getenv("PGADMIN_HOST", "127.0.0.1"),
        port=int(os.getenv("PGADMIN_PORT", "5432")),
        user=os.getenv("PGADMIN_USER", "postgres"),
        password=os.environ["PGADMIN_PASSWORD"],
        dbname=DB_NAME,
        connect_timeout=10,
        options="-c default_transaction_read_only=on",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT stock_code, trade_date,
                       close::double precision AS close_hfq,
                       amount::double precision AS amount
                FROM mkt.v_bar_hfq
                ORDER BY stock_code, trade_date
            """)
            bars = pd.DataFrame(cur.fetchall(), columns=[
                "stock_code", "trade_date", "close_hfq", "amount"
            ])
            cur.execute("""
                SELECT stock_code, trade_date,
                       close_hfq::double precision,
                       ma20::double precision, ma60::double precision,
                       ema12::double precision, ema26::double precision,
                       ret_20d::double precision, ret_60d::double precision,
                       vol_20d::double precision, amount_20d::double precision
                FROM mkt.mv_indicator_daily
                ORDER BY stock_code, trade_date
            """)
            indicators = pd.DataFrame(cur.fetchall(), columns=[
                "stock_code", "trade_date", *FIELDS.keys()
            ])
    return bars, indicators


def calculate_independently(bars: pd.DataFrame) -> pd.DataFrame:
    bars = bars.sort_values(["stock_code", "trade_date"]).copy()
    for c in ("close_hfq", "amount"):
        bars[c] = pd.to_numeric(bars[c], errors="raise")
    g = bars.groupby("stock_code", sort=False)

    bars["ma20"] = g["close_hfq"].transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    bars["ma60"] = g["close_hfq"].transform(
        lambda s: s.rolling(60, min_periods=60).mean())
    bars["ema12"] = g["close_hfq"].transform(
        lambda s: s.ewm(span=12, adjust=False).mean())
    bars["ema26"] = g["close_hfq"].transform(
        lambda s: s.ewm(span=26, adjust=False).mean())
    bars["ret_20d"] = g["close_hfq"].transform(
        lambda s: s / s.shift(20) - 1)
    bars["ret_60d"] = g["close_hfq"].transform(
        lambda s: s / s.shift(60) - 1)
    bars["vol_20d"] = g["close_hfq"].transform(
        lambda s: np.log(s / s.shift(1)).rolling(20, min_periods=20)
        .std(ddof=1) * np.sqrt(252))
    bars["amount_20d"] = g["amount"].transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    return bars


def verify() -> None:
    bars, indicators = read_data()
    if bars.empty or indicators.empty:
        raise AssertionError("后复权行情或指标物化视图为空，请先检查导入和刷新")

    bar_dups = int(bars.duplicated(["stock_code", "trade_date"]).sum())
    ind_dups = int(indicators.duplicated(["stock_code", "trade_date"]).sum())
    if bar_dups or ind_dups:
        raise AssertionError(f"重复主键：行情 {bar_dups}，指标 {ind_dups}")

    print(f"[DB] {DB_NAME}")
    print(f"[DATA] securities={bars['stock_code'].nunique()}, "
          f"bars={len(bars)}, indicators={len(indicators)}")
    if len(bars) != 9700 or bars["stock_code"].nunique() != 20:
        raise AssertionError("真实行情数量不是此次样本预期的 20 只 / 9700 条")

    expected = calculate_independently(bars)
    combined = expected.merge(
        indicators, how="outer", on=["stock_code", "trade_date"],
        suffixes=("_pd", "_sql"), indicator=True, validate="one_to_one"
    )
    if not combined["_merge"].eq("both").all():
        missing_sql = int(combined["_merge"].eq("left_only").sum())
        missing_bars = int(combined["_merge"].eq("right_only").sum())
        raise AssertionError(
            f"指标与行情日期不匹配：缺失指标 {missing_sql}，多余指标 {missing_bars}"
        )

    failed = []
    print("[CHECK] SQL vs pandas over all stocks and dates:")
    for name, (abs_tol, rel_tol) in FIELDS.items():
        pd_vals = pd.to_numeric(combined[f"{name}_pd"], errors="coerce")
        sql_vals = pd.to_numeric(combined[f"{name}_sql"], errors="coerce")
        mismatch_null = pd_vals.isna() != sql_vals.isna()
        finite_both = pd_vals.notna() & sql_vals.notna()
        diffs = (pd_vals[finite_both] - sql_vals[finite_both]).abs()
        max_error = float(diffs.max()) if not diffs.empty else float("nan")
        numeric_mismatch = ~np.isclose(
            pd_vals[finite_both].to_numpy(dtype=float),
            sql_vals[finite_both].to_numpy(dtype=float),
            atol=abs_tol, rtol=rel_tol, equal_nan=False,
        )
        bad_count = int(mismatch_null.sum()) + int(numeric_mismatch.sum())
        result = "PASS" if bad_count == 0 else "FAIL"
        print(f"  [{result}] {name:12s} max_abs_error={max_error:.4g} "
              f"mismatched_rows={bad_count}")
        if bad_count:
            failed.append(name)

    if failed:
        raise AssertionError("指标验证失败：" + ", ".join(failed))
    print("[ALL PASS] Real market indicators agree with pandas on 9700 rows")


if __name__ == "__main__":
    try:
        verify()
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
