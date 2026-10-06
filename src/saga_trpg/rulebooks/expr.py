"""A tiny, closed expression language for rulebook formulas.

Python expression syntax, restricted at compile time to integer arithmetic, comparisons,
boolean logic, conditional expressions, dotted/indexed lookups into plain data, and the
functions `max`, `min`, `abs`. No floats, no attribute access on objects, no calls to
anything else, no assignment, no loops. Evaluation is pure and deterministic.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_MAX_NODES = 200
_MAX_MAGNITUDE = 10**15
_FUNCTIONS: dict[str, Any] = {"max": max, "min": min, "abs": abs}
_BINARY = {ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod}
_COMPARE = {ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE}
_ALLOWED = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.Name,
    ast.Attribute,
    ast.Subscript,
    ast.Constant,
    ast.Load,
    ast.And,
    ast.Or,
    ast.Not,
    ast.USub,
    *_BINARY,
    *_COMPARE,
)


class ExpressionError(ValueError):
    """A formula is malformed, uses something outside the language, or cannot be evaluated."""


Value = int | bool | str


@dataclass(frozen=True)
class Expression:
    source: str
    tree: ast.Expression
    names: frozenset[str]

    def evaluate(self, scope: Mapping[str, Any]) -> Value:
        return _check_value(_evaluate(self.tree.body, scope), self.source)


def compile_expression(source: str) -> Expression:
    if not isinstance(source, str) or not source.strip():
        raise ExpressionError("formula must be a non-empty string")
    if len(source) > 500:
        raise ExpressionError("formula is too long")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ExpressionError(f"formula {source!r} is not valid: {exc.msg}") from None
    names: set[str] = set()
    count = 0
    for node in ast.walk(tree):
        count += 1
        if count > _MAX_NODES:
            raise ExpressionError(f"formula {source!r} is too complex")
        if not isinstance(node, _ALLOWED):
            raise ExpressionError(
                f"formula {source!r} uses {type(node).__name__}, which is not allowed"
            )
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, bool, str):
                raise ExpressionError(f"formula {source!r} may only contain integers and text")
            if isinstance(node.value, int) and abs(node.value) > _MAX_MAGNITUDE:
                raise ExpressionError(f"formula {source!r} contains a number that is too large")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
                raise ExpressionError(
                    f"formula {source!r} may only call {', '.join(sorted(_FUNCTIONS))}"
                )
            if node.keywords or not node.args:
                raise ExpressionError(f"formula {source!r} calls a function incorrectly")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise ExpressionError(f"formula {source!r} may not access {node.attr!r}")
        elif isinstance(node, ast.Name):
            if node.id.startswith("_") or node.id in _FUNCTIONS:
                continue
            names.add(node.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ExpressionError(f"formula {source!r} may not use {node.id!r}")
    return Expression(source=source, tree=tree, names=frozenset(names))


def _lookup(container: Any, key: Any, source: str) -> Any:
    if isinstance(container, Mapping) and isinstance(key, str) and key in container:
        return container[key]
    raise ExpressionError(f"formula {source!r} refers to unknown {key!r}")


def _check_value(value: Any, source: str) -> Value:
    if isinstance(value, bool) or isinstance(value, str):
        return value
    if isinstance(value, int):
        if abs(value) > _MAX_MAGNITUDE:
            raise ExpressionError(f"formula {source!r} produced a value that is too large")
        return value
    raise ExpressionError(f"formula {source!r} produced a non-integer value")


def _number(value: Any, source: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExpressionError(f"formula {source!r} needs an integer here")
    return value


def _evaluate(node: ast.AST, scope: Mapping[str, Any], source: str = "") -> Any:  # noqa: C901
    source = source or ast.unparse(node)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return _lookup(scope, node.id, source)
    if isinstance(node, ast.Attribute):
        return _lookup(_evaluate(node.value, scope, source), node.attr, source)
    if isinstance(node, ast.Subscript):
        key = _evaluate(node.slice, scope, source)
        return _lookup(_evaluate(node.value, scope, source), key, source)
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, scope, source)
        if isinstance(node.op, ast.Not):
            return not operand
        return _check_value(-_number(operand, source), source)
    if isinstance(node, ast.BoolOp):
        values = [_evaluate(item, scope, source) for item in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.IfExp):
        branch = node.body if _evaluate(node.test, scope, source) else node.orelse
        return _evaluate(branch, scope, source)
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, scope, source)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _evaluate(comparator, scope, source)
            if isinstance(op, (ast.Eq, ast.NotEq)):
                if type(left) is not type(right):
                    raise ExpressionError(f"formula {source!r} compares different kinds")
                ok = (left == right) if isinstance(op, ast.Eq) else (left != right)
            else:
                a, b = _number(left, source), _number(right, source)
                ok = {
                    ast.Lt: a < b,
                    ast.LtE: a <= b,
                    ast.Gt: a > b,
                    ast.GtE: a >= b,
                }[type(op)]
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.BinOp):
        a = _number(_evaluate(node.left, scope, source), source)
        b = _number(_evaluate(node.right, scope, source), source)
        if isinstance(node.op, ast.Add):
            result = a + b
        elif isinstance(node.op, ast.Sub):
            result = a - b
        elif isinstance(node.op, ast.Mult):
            result = a * b
        elif isinstance(node.op, ast.FloorDiv):
            if b == 0:
                raise ExpressionError(f"formula {source!r} divides by zero")
            result = a // b
        else:
            if b == 0:
                raise ExpressionError(f"formula {source!r} divides by zero")
            result = a % b
        return _check_value(result, source)
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name)
        args = [_number(_evaluate(item, scope, source), source) for item in node.args]
        return _check_value(_FUNCTIONS[node.func.id](*args), source)
    raise ExpressionError(f"formula {source!r} uses an unsupported construct")
