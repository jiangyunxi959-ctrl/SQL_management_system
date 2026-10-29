"""调度数据库计算。这里不把 Python 算好的因子值写回数据库。"""
from __future__ import annotations

import hashlib
from decimal import Decimal

from psycopg.types.json import Json

from app.factors.compiler import compile_query
from app.factors.quality import decode_mask
from app.factors.types import ValidatedExpression

ENGINE_VERSION = "d-sql-1"
_PANEL = """
SELECT stock_code, trade_date, day_ord,
       open_raw, high_raw, low_raw, close_raw,
       open_hfq, high_hfq, low_hfq, close_hfq,
       volume, amount, is_suspended, has_bar, back_factor, ca_in_window
FROM pit.fn_factor_panel(%s, %s)
ORDER BY stock_code, trade_date
"""
_FIELD_INDEX = {
    "open_raw": 3, "high_raw": 4, "low_raw": 5, "close_raw": 6,
    "open_hfq": 7, "high_hfq": 8, "low_hfq": 9, "close_hfq": 10,
    "volume": 11, "amount": 12,
}


def input_digest(expr: ValidatedExpression, panel_rows: list[tuple]) -> str:
    use_suspension = any(name.endswith("_raw") or name.endswith("_hfq") for name in expr.fields)
    lines = []
    for row in panel_rows:
        parts = [row[0], row[1].isoformat(), "1" if row[14] else "0"]
        for name in expr.fields:
            value = row[_FIELD_INDEX[name]]
            parts.append("" if value is None else format(Decimal(value), "f"))
        if use_suspension:
            parts.append("1" if row[13] else "0")
        if expr.ca_sensitive:
            parts.append("" if row[15] is None else format(Decimal(row[15]), "f"))
        lines.append("|".join(parts))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def compute(conn, expr: ValidatedExpression, universe: str):
    panel = conn.execute(_PANEL, (universe, expr.lookback)).fetchall()
    digest = input_digest(expr, panel)
    sql = compile_query(expr)
    rows = conn.execute(sql, (universe, expr.lookback)).fetchall()
    plan = "\n".join(item[0] for item in conn.execute("EXPLAIN " + sql, (universe, expr.lookback)))
    payload = []
    for stock_code, trade_date, value, mask in rows:
        codes, detail = decode_mask(int(mask or 0))
        if value is None:
            payload.append({
                "stock_code": stock_code, "trade_date": trade_date.isoformat(),
                "value": None, "quality": "MISSING", "reason_codes": codes, "reason_detail": detail,
            })
        else:
            payload.append({
                "stock_code": stock_code, "trade_date": trade_date.isoformat(),
                "value": format(Decimal(value), "f"), "quality": "OK",
                "reason_codes": [], "reason_detail": None,
            })
    return payload, digest, plan, panel


def save_run(conn, factor_id: int, version_no: int, as_of, universe: str, digest: str, payload: list) -> int:
    return conn.execute(
        "SELECT factor.fn_save_run(%s::bigint, %s::int, %s::date, %s::text, %s::text, %s::text, %s::jsonb)",
        (factor_id, version_no, as_of, universe, ENGINE_VERSION, digest, Json(payload)),
    ).fetchone()[0]
