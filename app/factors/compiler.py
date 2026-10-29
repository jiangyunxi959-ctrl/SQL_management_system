"""把已校验的结构编译成参数化 SQL。表达式原文不会拼进查询。"""
from __future__ import annotations

import re

from app.factors.types import Binary, Call, Field, Number, Unary, ValidatedExpression
from app.factors.zero_source import zero_bit

_NUMBER = re.compile(r"^(0|[1-9]\d*)(\.\d+)?$")
_FIELDS = {
    "open_raw": "open_raw", "high_raw": "high_raw", "low_raw": "low_raw", "close_raw": "close_raw",
    "open_hfq": "open_hfq", "high_hfq": "high_hfq", "low_hfq": "low_hfq", "close_hfq": "close_hfq",
    "volume": "volume", "amount": "amount",
}
_PRICE = {"open_raw", "high_raw", "low_raw", "close_raw", "open_hfq", "high_hfq", "low_hfq", "close_hfq"}
_KEEP = "stock_code, trade_date, day_ord, ca_in_window, is_suspended"


def compile_query(expr: ValidatedExpression) -> str:
    names: list[str] = []

    def emit(body: str) -> str:
        name = f"n{len(names)}"
        names.append(f"{name} AS ({body})")
        return name

    def walk(node) -> str:
        if isinstance(node, Number):
            if not _NUMBER.fullmatch(node.text):
                raise ValueError("数字未能通过编译前检查")
            return emit(
                f"SELECT {_KEEP}, {node.text}::numeric AS v, 0::integer AS m FROM panel"
            )
        if isinstance(node, Field):
            column = _FIELDS[node.name]
            if node.name in _PRICE:
                value = (
                    f"CASE WHEN NOT has_bar THEN NULL WHEN is_suspended THEN NULL "
                    f"WHEN {column} IS NULL THEN NULL ELSE {column} END"
                )
                mask = (
                    "CASE WHEN NOT has_bar THEN 2 WHEN is_suspended THEN 4 "
                    f"WHEN {column} IS NULL THEN 2 ELSE 0 END"
                )
            else:
                value = f"CASE WHEN NOT has_bar THEN NULL ELSE {column}::numeric END"
                mask = "CASE WHEN NOT has_bar THEN 2 ELSE 0 END"
            return emit(f"SELECT {_KEEP}, {value} AS v, ({mask})::integer AS m FROM panel")
        if isinstance(node, Unary):
            child = walk(node.expr)
            function = "v" if node.op == "+" else "(-v)"
            return emit(
                f"""
                SELECT {_KEEP},
                       CASE WHEN m <> 0 THEN NULL ELSE {function} END AS v,
                       m
                FROM {child}
                """
            )
        if isinstance(node, Binary):
            left, right = walk(node.left), walk(node.right)
            zero_flag = zero_bit(node.right) if node.op == "/" else 16
            arithmetic = {"+": "l.v + r.v", "-": "l.v - r.v", "*": "l.v * r.v", "/": "l.v / r.v"}[node.op]
            extra = ""
            if node.op == "/":
                extra = f" WHEN l.m = 0 AND r.m = 0 AND r.v = 0 THEN NULL"
                zero = f" | CASE WHEN l.m = 0 AND r.m = 0 AND r.v = 0 THEN {zero_flag} ELSE 0 END"
            else:
                zero = ""
            return emit(
                f"""
                SELECT l.stock_code, l.trade_date, l.day_ord, l.ca_in_window, l.is_suspended,
                       CASE WHEN (l.m | r.m) <> 0 THEN NULL{extra} ELSE {arithmetic} END AS v,
                       (l.m | r.m{zero})::integer AS m
                FROM {left} l JOIN {right} r USING (stock_code, trade_date)
                """
            )
        if isinstance(node, Call):
            child = walk(node.args[0])
            if node.name in {"log", "sqrt"}:
                guard = "v <= 0" if node.name == "log" else "v < 0"
                function = "ln(v)" if node.name == "log" else "sqrt(v)"
                return emit(
                    f"""
                    SELECT {_KEEP},
                           CASE WHEN m <> 0 OR {guard} THEN NULL ELSE {function} END AS v,
                           (m | CASE WHEN m = 0 AND {guard} THEN 32 WHEN m = 0 AND v IS NULL THEN 2 ELSE 0 END)::integer AS m
                    FROM {child}
                    """
                )
            if node.name == "abs":
                return emit(
                    f"SELECT {_KEEP}, CASE WHEN m <> 0 THEN NULL ELSE abs(v) END AS v, m FROM {child}"
                )
            argument = int(node.args[1].value)
            if node.name == "lag":
                return emit(
                    f"""
                    SELECT {_KEEP},
                           CASE WHEN day_ord <= {argument} THEN NULL
                                WHEN coalesce(lag(m, {argument}) OVER w, 2) <> 0 THEN NULL
                                ELSE lag(v, {argument}) OVER w END AS v,
                           CASE WHEN day_ord <= {argument} THEN 1
                                ELSE coalesce(lag(m, {argument}) OVER w, 2) END::integer AS m
                    FROM {child}
                    WINDOW w AS (PARTITION BY stock_code ORDER BY trade_date)
                    """
                )
            aggregate = "sum(v)" if node.name == "ts_sum" else (
                "stddev_samp(v)" if node.name == "ts_std" else "sum(v)"
            )
            divisor = f" / {argument}" if node.name == "ts_mean" else ""
            return emit(
                f"""
                SELECT {_KEEP},
                       CASE WHEN count(*) OVER w < {argument} THEN NULL
                            WHEN count(v) OVER w < {argument} THEN NULL
                            WHEN bit_or(m) OVER w <> 0 THEN NULL
                            ELSE {aggregate} OVER w{divisor} END AS v,
                       CASE WHEN count(*) OVER w < {argument} THEN bit_or(m) OVER w | 1
                            ELSE bit_or(m) OVER w | CASE
                                WHEN count(v) OVER w < {argument} AND bit_or(m) OVER w = 0 THEN 2 ELSE 0 END
                            END::integer AS m
                FROM {child}
                WINDOW w AS (PARTITION BY stock_code ORDER BY trade_date
                             ROWS BETWEEN {argument - 1} PRECEDING AND CURRENT ROW)
                """
            )
        raise ValueError("无法编译的节点")

    last = walk(expr.tree)
    price_result = expr.unit.q == 0 and (expr.unit.p or expr.unit.h)
    suspend_bit = "CASE WHEN is_suspended THEN 4 ELSE 0 END" if price_result else "0"
    corporate = "CASE WHEN ca_in_window THEN 128 ELSE 0 END" if expr.ca_sensitive else "0"
    body = ",\n".join(names)
    return f"""
    WITH panel AS (
        SELECT * FROM pit.fn_factor_panel(%s, %s)
    ),
    {body}
    SELECT stock_code, trade_date,
           CASE WHEN (m | ({suspend_bit}) | ({corporate})) <> 0 THEN NULL ELSE v END AS value,
           (m | ({suspend_bit}) | ({corporate}))::integer AS mask
    FROM {last}
    WHERE trade_date = (SELECT max(trade_date) FROM panel)
    """
