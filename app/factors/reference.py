"""独立核对用的 Python 计算。正式结果只保存数据库算出的值。"""
from __future__ import annotations

from decimal import Decimal

from app.factors.types import Binary, Call, Field, Number, Unary, ValidatedExpression
from app.factors.zero_source import zero_bit

ZERO = Decimal(0)


def evaluate(expr: ValidatedExpression, rows: list[dict]) -> tuple[Decimal | None, int]:
    ordered = sorted(rows, key=lambda row: row["trade_date"])
    series = _series(expr.tree, ordered)
    value, mask = series[-1]
    if expr.ca_sensitive and ordered[-1].get("ca_in_window"):
        mask |= 128
        value = None
    if expr.unit.q == 0 and (expr.unit.p or expr.unit.h) and ordered[-1].get("is_suspended"):
        mask |= 4
        value = None
    if mask:
        value = None
    return value, mask


def _series(node, rows: list[dict]) -> list[tuple[Decimal | None, int]]:
    if isinstance(node, Number):
        number = Decimal(node.text)
        return [(number, 0) for _ in rows]
    if isinstance(node, Field):
        return [_field(node.name, row) for row in rows]
    if isinstance(node, Unary):
        child = _series(node.expr, rows)
        out = []
        for value, mask in child:
            if mask or value is None:
                out.append((None, mask or (2 if value is None else 0)))
            else:
                out.append((value if node.op == "+" else -value, 0))
        return out
    if isinstance(node, Binary):
        left, right = _series(node.left, rows), _series(node.right, rows)
        divisor_bit = zero_bit(node.right)
        out = []
        for (lv, lm), (rv, rm) in zip(left, right):
            mask = lm | rm
            if mask:
                out.append((None, mask, ))
                continue
            if node.op == "/" and rv == ZERO:
                out.append((None, divisor_bit))
                continue
            if node.op == "+":
                out.append((lv + rv, 0))
            elif node.op == "-":
                out.append((lv - rv, 0))
            elif node.op == "*":
                out.append((lv * rv, 0))
            else:
                out.append((lv / rv, 0))
        return out
    child = _series(node.args[0], rows)
    if node.name in {"abs", "log", "sqrt"}:
        out = []
        for value, mask in child:
            if mask or value is None:
                out.append((None, mask or 2))
            elif node.name == "abs":
                out.append((abs(value), 0))
            elif node.name == "log" and value <= ZERO:
                out.append((None, 32))
            elif node.name == "sqrt" and value < ZERO:
                out.append((None, 32))
            elif node.name == "log":
                out.append((value.ln(), 0))
            else:
                out.append((value.sqrt(), 0))
        return out
    argument = int(node.args[1].value)
    if node.name == "lag":
        out = []
        for index, _row in enumerate(rows):
            if index < argument:
                out.append((None, 1))
            else:
                out.append(child[index - argument])
        return out
    out = []
    for index in range(len(rows)):
        if index + 1 < argument:
            out.append((None, 1))
            continue
        window = child[index + 1 - argument:index + 1]
        mask = 0
        values = []
        for value, item_mask in window:
            mask |= item_mask
            if value is None and item_mask == 0:
                mask |= 2
            if value is not None and item_mask == 0:
                values.append(value)
        if mask or len(values) != argument:
            out.append((None, mask or 2))
            continue
        if node.name == "ts_sum":
            out.append((sum(values, ZERO), 0))
        elif node.name == "ts_mean":
            out.append((sum(values, ZERO) / Decimal(argument), 0))
        else:
            mean = sum(values, ZERO) / Decimal(argument)
            variance = sum((item - mean) ** 2 for item in values) / Decimal(argument - 1)
            out.append((variance.sqrt(), 0))
    return out


def _field(name: str, row: dict) -> tuple[Decimal | None, int]:
    if not row.get("has_bar"):
        return None, 2
    if name not in {"volume", "amount"} and row.get("is_suspended"):
        return None, 4
    value = row.get(name)
    if value is None:
        return None, 2
    return Decimal(value), 0
