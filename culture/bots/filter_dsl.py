"""Safe expression DSL for bot event filters.

Grammar (recursive descent):

    expr       := or_expr
    or_expr    := and_expr ('or' and_expr)*
    and_expr   := not_expr ('and' not_expr)*
    not_expr   := 'not' not_expr | cmp_expr
    cmp_expr   := atom (('==' | '!=' | 'in') atom)?
    atom       := STRING | NUMBER | LIST | IDENT ('.' IDENT)* | '(' expr ')'
    LIST       := '[' [atom (',' atom)*] ']'

Evaluates against a dict (the event). Missing fields short-circuit to
`_MISSING`, which compares `False` to everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_MISSING = object()


class FilterParseError(Exception):
    def __init__(self, message: str, column: int = 0, expected: str = "") -> None:
        super().__init__(message, column, expected)  # noqa: B042
        self.column = column
        self.expected = expected

    def __str__(self) -> str:
        msg = self.args[0] if self.args else ""
        if self.expected:
            return f"{msg} at col {self.column} (expected {self.expected})"
        return f"{msg} at col {self.column}"


# -------- AST nodes --------


@dataclass
class Literal:
    value: Any


@dataclass
class FieldRef:
    parts: tuple[str, ...]


@dataclass
class ListExpr:
    items: list


@dataclass
class Compare:
    op: str  # '==', '!=', 'in'
    left: Any
    right: Any


@dataclass
class And:
    left: Any
    right: Any


@dataclass
class Or:
    left: Any
    right: Any


@dataclass
class Not:
    expr: Any


# -------- tokenizer --------


class _Tok:
    STRING = "STRING"
    NUMBER = "NUMBER"
    IDENT = "IDENT"
    OP = "OP"
    KW = "KW"
    LP = "LP"
    RP = "RP"
    LBR = "LBR"
    RBR = "RBR"
    COMMA = "COMMA"
    DOT = "DOT"
    END = "END"


_KEYWORDS = {"and", "or", "not", "in"}

_SIMPLE_CHARS = {
    "(": _Tok.LP,
    ")": _Tok.RP,
    "[": _Tok.LBR,
    "]": _Tok.RBR,
    ",": _Tok.COMMA,
    ".": _Tok.DOT,
}


def _tok_string(src, i):
    end = src.find("'", i + 1)
    if end == -1:
        raise FilterParseError("unterminated string", i, "closing quote")
    return (_Tok.STRING, src[i + 1 : end], i), end + 1


def _tok_number(src, i, n):
    j = i
    while j < n and src[j].isdigit():
        j += 1
    return (_Tok.NUMBER, int(src[i:j]), i), j


def _tok_word(src, i, n):
    j = i
    while j < n and (src[j].isalnum() or src[j] in "_-"):
        j += 1
    word = src[i:j]
    kind = _Tok.KW if word in _KEYWORDS else _Tok.IDENT
    return (kind, word, i), j


def _tok_operator(src, i):
    two = src[i : i + 2]
    if two in ("==", "!="):
        return (_Tok.OP, two, i), i + 2
    return None, i


def _tokenize(src: str) -> list[tuple]:
    tokens = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "'":
            tok, i = _tok_string(src, i)
            tokens.append(tok)
            continue
        if ch.isdigit():
            tok, i = _tok_number(src, i, n)
            tokens.append(tok)
            continue
        if ch.isalpha() or ch == "_":
            tok, i = _tok_word(src, i, n)
            tokens.append(tok)
            continue
        tok, new_i = _tok_operator(src, i)
        if tok is not None:
            tokens.append(tok)
            i = new_i
            continue
        simple = _SIMPLE_CHARS.get(ch)
        if simple is not None:
            tokens.append((simple, ch, i))
            i += 1
            continue
        raise FilterParseError(f"unexpected character {ch!r}", i, "operator / identifier")
    tokens.append((_Tok.END, "", n))
    return tokens


# -------- parser --------


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos]

    def consume(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, kind, expected_label):
        tok = self.peek()
        if tok[0] != kind:
            raise FilterParseError(f"unexpected {tok[1]!r}", tok[2], expected_label)
        return self.consume()

    def parse(self):
        expr = self._or()
        if self.peek()[0] != _Tok.END:
            tok = self.peek()
            raise FilterParseError(f"trailing {tok[1]!r}", tok[2], "end of expression")
        return expr

    def _or(self):
        left = self._and()
        while self.peek()[0] == _Tok.KW and self.peek()[1] == "or":
            self.consume()
            right = self._and()
            left = Or(left, right)
        return left

    def _and(self):
        left = self._not()
        while self.peek()[0] == _Tok.KW and self.peek()[1] == "and":
            self.consume()
            right = self._not()
            left = And(left, right)
        return left

    def _not(self):
        if self.peek()[0] == _Tok.KW and self.peek()[1] == "not":
            self.consume()
            return Not(self._not())
        return self._cmp()

    def _cmp(self):
        left = self._atom()
        tok = self.peek()
        if tok[0] == _Tok.OP and tok[1] in ("==", "!="):
            self.consume()
            right = self._atom()
            return Compare(tok[1], left, right)
        if tok[0] == _Tok.KW and tok[1] == "in":
            self.consume()
            right = self._atom()
            return Compare("in", left, right)
        return left

    def _atom(self):
        tok = self.peek()
        if tok[0] == _Tok.STRING:
            self.consume()
            return Literal(tok[1])
        if tok[0] == _Tok.NUMBER:
            self.consume()
            return Literal(tok[1])
        if tok[0] == _Tok.LP:
            self.consume()
            inner = self._or()
            self.expect(_Tok.RP, ")")
            return inner
        if tok[0] == _Tok.LBR:
            self.consume()
            items = []
            if self.peek()[0] != _Tok.RBR:
                items.append(self._atom())
                while self.peek()[0] == _Tok.COMMA:
                    self.consume()
                    items.append(self._atom())
            self.expect(_Tok.RBR, "]")
            return ListExpr(items)
        if tok[0] == _Tok.IDENT:
            self.consume()
            parts = [tok[1]]
            while self.peek()[0] == _Tok.DOT:
                self.consume()
                ident = self.expect(_Tok.IDENT, "identifier after '.'")
                parts.append(ident[1])
            # Reject function calls: next token must not be LP
            if self.peek()[0] == _Tok.LP:
                raise FilterParseError(
                    f"function calls not allowed: {tok[1]!r}",
                    tok[2],
                    "operator or end of expression",
                )
            return FieldRef(tuple(parts))
        raise FilterParseError(f"unexpected {tok[1]!r}", tok[2], "value")


def compile_filter(source: str):
    """Parse *source* into an AST node ready for :func:`evaluate`."""
    tokens = _tokenize(source)
    return _Parser(tokens).parse()


# -------- evaluator --------


def _resolve(ref: FieldRef, event: dict) -> Any:
    cur: Any = event
    for part in ref.parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _to_bool(val) -> bool:
    if val is _MISSING:
        return False
    return bool(val)


def _eval_compare(node: Compare, event: dict) -> Any:
    left = evaluate(node.left, event)
    right = evaluate(node.right, event)
    if left is _MISSING or right is _MISSING:
        return False
    if node.op == "==":
        return left == right
    if node.op == "!=":
        return left != right
    if node.op == "in":
        try:
            return left in right
        except TypeError:
            return False
    return False


def evaluate(node, event: dict) -> Any:
    """Evaluate an AST *node* against *event* dict, returning a Python value."""
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, FieldRef):
        return _resolve(node, event)
    if isinstance(node, ListExpr):
        return [evaluate(i, event) for i in node.items]
    if isinstance(node, Compare):
        return _eval_compare(node, event)
    if isinstance(node, And):
        return _to_bool(evaluate(node.left, event)) and _to_bool(evaluate(node.right, event))
    if isinstance(node, Or):
        return _to_bool(evaluate(node.left, event)) or _to_bool(evaluate(node.right, event))
    if isinstance(node, Not):
        return not _to_bool(evaluate(node.expr, event))
    return False
