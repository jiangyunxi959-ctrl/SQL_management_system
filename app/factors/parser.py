"""手写递归下降解析器。乘除优先于加减，同级从左到右。"""
from __future__ import annotations

from app.factors.errors import FactorError
from app.factors.lexer import MAX_DEPTH, MAX_NODES, Token, tokenize
from app.factors.types import Binary, Call, Field, IntLiteral, Number, Span, Unary

_SCALARS = {"abs", "log", "sqrt"}
_WINDOWS = {"ts_sum", "ts_mean", "ts_std"}


class Parser:
    def __init__(self, source: str):
        self.source = source
        self.tokens = tokenize(source)
        self.index = 0
        self.nodes = 0

    def parse(self):
        if not self.source.strip():
            raise FactorError("SYNTAX", "表达式不能为空", 0, len(self.source))
        node, _depth = self._sum()
        if self._peek().kind != "EOF":
            self._reject(self._peek())
        return node

    def _sum(self):
        left, depth = self._product()
        while self._peek().kind in {"PLUS", "MINUS"}:
            op = self._pop()
            right, right_depth = self._product()
            left = self._node(Binary(op.text, left, right, Span(left.span.start, right.span.end)), max(depth, right_depth))
            depth = left[1]
            left = left[0]
        return left, depth

    def _product(self):
        left, depth = self._unary()
        while self._peek().kind in {"STAR", "SLASH"}:
            op = self._pop()
            right, right_depth = self._unary()
            left = self._node(Binary(op.text, left, right, Span(left.span.start, right.span.end)), max(depth, right_depth))
            depth = left[1]
            left = left[0]
        return left, depth

    def _unary(self):
        if self._peek().kind in {"PLUS", "MINUS"}:
            op = self._pop()
            expr, depth = self._unary()
            node, depth = self._node(Unary(op.text, expr, Span(op.start, expr.span.end)), depth)
            return node, depth
        return self._primary()

    def _primary(self):
        token = self._peek()
        if token.kind == "NUMBER":
            self._pop()
            from decimal import Decimal

            node, depth = self._node(Number(token.text, Decimal(token.text), Span(token.start, token.end)), 0)
            return node, depth
        if token.kind == "IDENT":
            self._pop()
            if self._peek().kind == "LPAREN":
                return self._call(token)
            node, depth = self._node(Field(token.text, Span(token.start, token.end)), 0)
            return node, depth
        if token.kind == "LPAREN":
            self._pop()
            node, depth = self._sum()
            self._expect("RPAREN", "括号没有成对闭合")
            return node, depth
        self._reject(token)
        raise AssertionError("unreachable")

    def _call(self, name: Token):
        self._expect("LPAREN", "函数调用缺少左括号")
        if name.text in _SCALARS:
            arg, depth = self._sum()
            if self._peek().kind == "COMMA":
                bad = self._pop()
                raise FactorError("ARGUMENT", f"{name.text} 只接受一个参数", bad.start, bad.end)
            end = self._expect("RPAREN", "函数调用缺少右括号")
            return self._node(Call(name.text, (arg,), Span(name.start, end.end)), depth)
        if name.text == "lag" or name.text in _WINDOWS:
            arg, depth = self._sum()
            if self._peek().kind != "COMMA":
                bad = self._peek()
                raise FactorError("ARGUMENT", f"{name.text} 需要表达式和整数两个参数", bad.start, bad.end)
            self._pop()
            literal = self._integer(name.text)
            if self._peek().kind == "COMMA":
                bad = self._pop()
                raise FactorError("ARGUMENT", f"{name.text} 只接受两个参数", bad.start, bad.end)
            if self._peek().kind in {"PLUS", "MINUS", "STAR", "SLASH", "NUMBER", "IDENT", "LPAREN"}:
                bad = self._peek()
                raise FactorError("ARGUMENT", "窗口或滞后参数必须是整数字面量，不能是表达式", bad.start, bad.end)
            end = self._expect("RPAREN", "函数调用缺少右括号")
            return self._node(Call(name.text, (arg, literal), Span(name.start, end.end)), max(depth, 1))
        raise FactorError("FUNCTION", "未知函数，或函数必须使用规范中的小写名称", name.start, name.end)

    def _integer(self, fn: str) -> IntLiteral:
        token = self._peek()
        if token.kind == "MINUS":
            raise FactorError("ARGUMENT", "不允许未来取值或表达式形式的窗口参数", token.start, token.end)
        if token.kind in {"PLUS", "LPAREN", "IDENT"}:
            raise FactorError("ARGUMENT", "窗口或滞后参数必须是整数字面量，不能是表达式", token.start, token.end)
        if token.kind != "NUMBER":
            raise FactorError("ARGUMENT", "窗口或滞后参数必须是整数字面量", token.start, token.end)
        if "." in token.text:
            raise FactorError("ARGUMENT", "窗口必须是整数字面量", token.start, token.end)
        self._pop()
        node, _depth = self._node(IntLiteral(token.text, int(token.text), Span(token.start, token.end)), 0)
        if not isinstance(node, IntLiteral):
            raise FactorError("ARGUMENT", f"{fn} 的参数不合法", token.start, token.end)
        return node

    def _node(self, node, child_depth: int):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise FactorError("COMPLEXITY", "表达式节点数不能超过 32", node.span.start, node.span.end)
        depth = child_depth + 1
        if depth > MAX_DEPTH:
            raise FactorError("COMPLEXITY", "表达式深度不能超过 8", node.span.start, node.span.end)
        return node, depth

    def _peek(self) -> Token:
        return self.tokens[self.index]

    def _pop(self) -> Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _expect(self, kind: str, message: str) -> Token:
        token = self._peek()
        if token.kind != kind:
            raise FactorError("SYNTAX", message, token.start, token.end or token.start + 1)
        return self._pop()

    def _reject(self, token: Token) -> None:
        if token.kind == "EOF":
            raise FactorError("SYNTAX", "表达式不完整", token.start, token.end)
        if token.text in "><=!":
            raise FactorError("SYNTAX", "筛选条件不属于因子表达式", token.start, token.end)
        if token.text == ";":
            raise FactorError("SYNTAX", "不允许多条表达式或数据库命令", token.start, token.end)
        if token.text == ".":
            raise FactorError("SYNTAX", "不能引用对象属性或其他公式", token.start, token.end)
        raise FactorError("SYNTAX", "这里不能出现该符号", token.start, token.end or token.start + 1)


def parse(source: str):
    return Parser(source).parse()
