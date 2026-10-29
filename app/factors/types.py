"""表达式节点与校验结果。数字保留原文，不改精度。"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Span:
    start: int
    end: int


@dataclass(frozen=True)
class Unit:
    """单位指数：原始价格 P、调整后价格 H、股数 Q。成交额是 P+Q。"""

    p: int = 0
    h: int = 0
    q: int = 0

    def __add__(self, other: "Unit") -> "Unit":
        return Unit(self.p + other.p, self.h + other.h, self.q + other.q)

    def __sub__(self, other: "Unit") -> "Unit":
        return Unit(self.p - other.p, self.h - other.h, self.q - other.q)

    def carries_raw(self) -> bool:
        return self.p != 0 or self.q != 0

    def label(self) -> str:
        if self == Unit():
            return "无单位"
        if self == Unit(1, 0, 0):
            return "元/股（未调整价格）"
        if self == Unit(0, 1, 0):
            return "元/调整后价格单位"
        if self == Unit(0, 0, 1):
            return "股"
        if self == Unit(1, 0, 1):
            return "元"
        return f"复合单位(原始价格^{self.p}, 调整后价格^{self.h}, 股数^{self.q})"

    def basis(self) -> str:
        if self.p and self.h:
            return "MIXED"
        if self.p:
            return "RAW"
        if self.h:
            return "HFQ"
        if self.q:
            return "SHARE"
        return "NONE"


@dataclass(frozen=True)
class Number:
    text: str
    value: Decimal
    span: Span


@dataclass(frozen=True)
class IntLiteral:
    text: str
    value: int
    span: Span


@dataclass(frozen=True)
class Field:
    name: str
    span: Span


@dataclass(frozen=True)
class Unary:
    op: str
    expr: "Node"
    span: Span


@dataclass(frozen=True)
class Binary:
    op: str
    left: "Node"
    right: "Node"
    span: Span


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple
    span: Span


Node = Number | Field | Unary | Binary | Call


@dataclass(frozen=True)
class ValidatedExpression:
    source: str
    tree: Node
    normalized: dict
    digest: str
    syntax_version: str
    unit: Unit
    unit_label: str
    price_basis: str
    lookback: int
    fields: tuple[str, ...]
    explanation: str
    ca_sensitive: bool
    node_count: int
    depth: int
