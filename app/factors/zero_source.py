"""除数为零时按字段来源区分成交量和成交额，不按单位里是否含有股数一刀切。"""
from __future__ import annotations

from app.factors.types import Binary, Call, Field, Unary


def zero_source(node) -> int:
    """1 表示结果来自成交量，2 表示来自成交额，0 表示都不是。"""
    if isinstance(node, Field):
        if node.name == "volume":
            return 1
        if node.name == "amount":
            return 2
        return 0
    if isinstance(node, Unary):
        return zero_source(node.expr)
    if isinstance(node, Binary):
        if node.op in {"+", "-"}:
            left, right = zero_source(node.left), zero_source(node.right)
            return left if left and left == right else 0
        return 0
    if isinstance(node, Call) and node.name in {"lag", "abs", "ts_sum", "ts_mean"}:
        return zero_source(node.args[0])
    return 0


def zero_bit(node) -> int:
    source = zero_source(node)
    if source == 1:
        return 8
    if source == 2:
        return 256
    return 16
