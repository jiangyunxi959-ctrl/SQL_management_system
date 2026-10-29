"""单位、窗口和历史长度。公共公式与私有公式使用同一套规则。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from app.factors.errors import FactorError
from app.factors.lexer import SYNTAX
from app.factors.parser import parse
from app.factors.types import (
    Binary,
    Call,
    Field,
    IntLiteral,
    Node,
    Number,
    Unary,
    Unit,
    ValidatedExpression,
)

FIELDS = {
    "open_raw": Unit(1, 0, 0),
    "high_raw": Unit(1, 0, 0),
    "low_raw": Unit(1, 0, 0),
    "close_raw": Unit(1, 0, 0),
    "open_hfq": Unit(0, 1, 0),
    "high_hfq": Unit(0, 1, 0),
    "low_hfq": Unit(0, 1, 0),
    "close_hfq": Unit(0, 1, 0),
    "volume": Unit(0, 0, 1),
    "amount": Unit(1, 0, 1),
}
_PRICE = {"open_raw", "high_raw", "low_raw", "close_raw", "open_hfq", "high_hfq", "low_hfq", "close_hfq"}
_NAMES = {
    "open_raw": "开盘价",
    "high_raw": "最高价",
    "low_raw": "最低价",
    "close_raw": "收盘价",
    "open_hfq": "调整后开盘价",
    "high_hfq": "调整后最高价",
    "low_hfq": "调整后最低价",
    "close_hfq": "调整后收盘价",
    "volume": "成交股数",
    "amount": "成交额",
}


@dataclass(frozen=True)
class Info:
    unit: Unit
    lookback: int
    windows: int
    lags: int
    ca_sensitive: bool
    raw_offsets: frozenset[int] | None
    depth: int
    nodes: int


def validate(source: str) -> ValidatedExpression:
    tree = parse(source)
    info = _info(tree)
    _reject_mixed(info.unit, _span(tree))
    if info.lookback > 252:
        raise FactorError("COMPLEXITY", "所需历史不能超过 252 个交易日", _span(tree).start, _span(tree).end)
    normalized = _normalize(tree)
    payload = json.dumps({"syntax": SYNTAX, "tree": normalized}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ValidatedExpression(
        source=source,
        tree=tree,
        normalized=normalized,
        digest=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        syntax_version=SYNTAX,
        unit=info.unit,
        unit_label=info.unit.label(),
        price_basis=info.unit.basis(),
        lookback=info.lookback,
        fields=tuple(sorted(_fields(tree))),
        explanation=_explain(tree),
        ca_sensitive=info.ca_sensitive,
        node_count=info.nodes,
        depth=info.depth,
    )


def _info(node: Node) -> Info:
    if isinstance(node, Number):
        return Info(Unit(), 0, 0, 0, False, None, 1, 1)
    if isinstance(node, Field):
        if node.name not in FIELDS:
            if node.name.lower() in FIELDS or node.name.lower() in {"abs", "log", "sqrt", "lag", "ts_sum", "ts_mean", "ts_std"}:
                raise FactorError("FIELD", "字段和函数必须使用小写", node.span.start, node.span.end)
            raise FactorError("FIELD", "未知字段", node.span.start, node.span.end)
        unit = FIELDS[node.name]
        offsets = frozenset({0}) if unit.carries_raw() else None
        return Info(unit, 1, 0, 0, False, offsets, 1, 1)
    if isinstance(node, Unary):
        child = _info(node.expr)
        return Info(child.unit, child.lookback, child.windows, child.lags, child.ca_sensitive, child.raw_offsets, child.depth + 1, child.nodes + 1)
    if isinstance(node, Binary):
        left, right = _info(node.left), _info(node.right)
        if node.op in {"+", "-"}:
            if left.unit != right.unit:
                raise FactorError("UNIT", "加减两侧的单位必须一致，常数也不能用来绕过单位检查", node.span.start, node.span.end)
            unit = left.unit
        elif node.op == "*":
            unit = left.unit + right.unit
        else:
            unit = left.unit - right.unit
        _reject_mixed(unit, node.span)
        ca = left.ca_sensitive or right.ca_sensitive
        if left.raw_offsets is not None and right.raw_offsets is not None and left.raw_offsets != right.raw_offsets:
            ca = True
        if unit.carries_raw():
            offsets: frozenset[int] | None = frozenset()
            parts = []
            if left.unit.carries_raw() and left.raw_offsets is not None:
                parts.append(left.raw_offsets)
            if right.unit.carries_raw() and right.raw_offsets is not None:
                parts.append(right.raw_offsets)
            offsets = frozenset().union(*parts) if parts else None
        else:
            offsets = None
        return Info(
            unit,
            max(left.lookback, right.lookback),
            max(left.windows, right.windows),
            max(left.lags, right.lags),
            ca,
            offsets,
            max(left.depth, right.depth) + 1,
            left.nodes + right.nodes + 1,
        )
    if isinstance(node, Call):
        if node.name in {"abs", "log", "sqrt"}:
            child = _info(node.args[0])
            if node.name in {"log", "sqrt"} and child.unit != Unit():
                raise FactorError("UNIT", "对数和开方只能用于无单位数值", node.span.start, node.span.end)
            return Info(child.unit, child.lookback, child.windows, child.lags, child.ca_sensitive, child.raw_offsets, child.depth + 1, child.nodes + 1)
        child = _info(node.args[0])
        literal: IntLiteral = node.args[1]
        if node.name == "lag":
            if child.lags >= 1:
                raise FactorError("TEMPORAL", "同一条计算路径不能嵌套滞后", node.span.start, node.span.end)
            if not 1 <= literal.value <= 251:
                raise FactorError("ARGUMENT", "滞后天数必须是 1 到 251 的整数", literal.span.start, literal.span.end)
            offsets = None if child.raw_offsets is None else frozenset(item + literal.value for item in child.raw_offsets)
            return Info(
                child.unit,
                child.lookback + literal.value,
                child.windows,
                child.lags + 1,
                child.ca_sensitive,
                offsets,
                max(child.depth, 1) + 1,
                child.nodes + 2,
            )
        if child.windows >= 1:
            raise FactorError("TEMPORAL", "同一条计算路径不能嵌套窗口", node.span.start, node.span.end)
        upper = 252
        lower = 2 if node.name == "ts_std" else 1
        if not lower <= literal.value <= upper:
            message = "样本标准差的窗口至少为 2，且不超过 252" if node.name == "ts_std" else "窗口长度必须是 1 到 252 的整数"
            raise FactorError("ARGUMENT", message, literal.span.start, literal.span.end)
        if child.raw_offsets is None:
            offsets = None
            ca = child.ca_sensitive
        else:
            offsets = frozenset(item + shift for item in child.raw_offsets for shift in range(literal.value))
            ca = True
        return Info(
            child.unit,
            child.lookback + literal.value - 1,
            child.windows + 1,
            child.lags,
            ca,
            offsets,
            max(child.depth, 1) + 1,
            child.nodes + 2,
        )
    raise FactorError("SYNTAX", "无法识别的表达式", 0, 0)


def _reject_mixed(unit: Unit, span) -> None:
    if unit.p and unit.h:
        raise FactorError("UNIT", "两种价格口径不能混用", span.start, span.end)


def _fields(node: Node) -> set[str]:
    if isinstance(node, Field):
        return {node.name}
    if isinstance(node, Number):
        return set()
    if isinstance(node, Unary):
        return _fields(node.expr)
    if isinstance(node, Binary):
        return _fields(node.left) | _fields(node.right)
    if isinstance(node, Call):
        return _fields(node.args[0])
    return set()


def _normalize(node: Node):
    if isinstance(node, Number):
        return {"type": "num", "text": node.text}
    if isinstance(node, IntLiteral):
        return {"type": "int", "text": node.text}
    if isinstance(node, Field):
        return {"type": "field", "name": node.name}
    if isinstance(node, Unary):
        return {"type": "unary", "op": node.op, "expr": _normalize(node.expr)}
    if isinstance(node, Binary):
        return {"op": node.op, "left": _normalize(node.left), "right": _normalize(node.right), "type": "binary"}
    return {"type": "call", "name": node.name, "args": [_normalize(arg) if not isinstance(arg, IntLiteral) else _normalize(arg) for arg in node.args]}


def _explain(node: Node) -> str:
    if isinstance(node, Number):
        return node.text
    if isinstance(node, Field):
        return _NAMES[node.name]
    if isinstance(node, Unary):
        return f"（{'正' if node.op == '+' else '负'}）{_explain(node.expr)}"
    if isinstance(node, Binary):
        op = {"+": "加", "-": "减", "*": "乘", "/": "除"}[node.op]
        return f"{_explain(node.left)}{op}{_explain(node.right)}"
    if isinstance(node, Call):
        inner = _explain(node.args[0])
        if node.name == "abs":
            return f"{inner}的绝对值"
        if node.name == "log":
            return f"{inner}的自然对数"
        if node.name == "sqrt":
            return f"{inner}的平方根"
        if node.name == "lag":
            return f"{node.args[1].value}个交易日前的{inner}"
        label = {"ts_sum": "合计", "ts_mean": "均值", "ts_std": "样本标准差"}[node.name]
        return f"含当天在内{node.args[1].value}个交易日{inner}的{label}"
    return ""


def _span(node: Node):
    return node.span


def analyze(node: Node) -> Info:
    return _info(node)


def decimal_text(value: Decimal) -> str:
    return format(value, "f")
