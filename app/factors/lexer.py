"""只接受规范里的 ASCII 符号。不借用 Python 的数字或名字规则。"""
from __future__ import annotations

from dataclasses import dataclass

from app.factors.errors import FactorError

SYNTAX = "v1"
MAX_LENGTH = 1024
MAX_NODES = 32
MAX_DEPTH = 8
MAX_ABS = 10**12
MAX_DECIMALS = 12


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int


_SYMBOLS = {
    "+": "PLUS",
    "-": "MINUS",
    "*": "STAR",
    "/": "SLASH",
    "(": "LPAREN",
    ")": "RPAREN",
    ",": "COMMA",
}


def tokenize(source: str) -> list[Token]:
    if len(source) > MAX_LENGTH:
        raise FactorError("COMPLEXITY", "表达式不能超过 1024 个字符", MAX_LENGTH, len(source))
    tokens: list[Token] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ord(ch) > 127:
            raise FactorError("LEXICAL", "只允许 ASCII 字符，不能使用容易混淆的符号", i, i + 1)
        if ch in _SYMBOLS:
            tokens.append(Token(_SYMBOLS[ch], ch, i, i + 1))
            i += 1
            continue
        if ch == ".":
            if i + 1 < n and source[i + 1].isdigit():
                raise FactorError("LEXICAL", "数字必须以整数部分开头", i, i + 2)
            raise FactorError("SYNTAX", "不能引用对象属性或其他公式", i, i + 1)
        if ch in "><=!":
            raise FactorError("SYNTAX", "筛选条件不属于因子表达式", i, i + 1)
        if ch == ";":
            raise FactorError("SYNTAX", "不允许多条表达式或数据库命令", i, i + 1)
        if ch == "%":
            raise FactorError("LEXICAL", "不接受百分号，请改用小数", i, i + 1)
        if ch.isdigit():
            tokens.append(_number(source, i))
            i = tokens[-1].end
            continue
        if ch.isascii() and (ch.isalpha() or ch == "_"):
            j = i + 1
            while j < n and source[j].isascii() and (source[j].isalnum() or source[j] == "_"):
                j += 1
            tokens.append(Token("IDENT", source[i:j], i, j))
            i = j
            continue
        raise FactorError("LEXICAL", "不支持的符号", i, i + 1)
    tokens.append(Token("EOF", "", n, n))
    return tokens


def _number(source: str, start: int) -> Token:
    n = len(source)
    i = start
    if source[i] == "0" and i + 1 < n and source[i + 1].isdigit():
        raise FactorError("LEXICAL", "整数不能有多余的前导零", start, i + 2)
    while i < n and source[i].isdigit():
        i += 1
    if i < n and source[i] in "eE":
        raise FactorError("LEXICAL", "不接受科学计数法", start, i + 1)
    if i < n and source[i] == ".":
        dot = i
        i += 1
        frac = i
        if i >= n or not source[i].isdigit():
            raise FactorError("LEXICAL", "小数点后至少要有一位数字", start, min(n, i + 1))
        while i < n and source[i].isdigit():
            i += 1
        if i - frac > MAX_DECIMALS:
            raise FactorError("LEXICAL", "小数最多 12 位", start, i)
        if i < n and source[i] in "eE":
            raise FactorError("LEXICAL", "不接受科学计数法", start, i + 1)
        if dot == start:
            raise FactorError("LEXICAL", "数字必须以整数部分开头", start, i)
    text = source[start:i]
    from decimal import Decimal

    if Decimal(text) > MAX_ABS:
        raise FactorError("LEXICAL", "数字绝对值不能超过 10 的 12 次方", start, i)
    return Token("NUMBER", text, start, i)
