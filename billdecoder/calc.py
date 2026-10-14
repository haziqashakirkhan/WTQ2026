"""Tiny safe arithmetic evaluator so the LLM never does maths in its head.

The model proposes named expressions (e.g. "pct(total_taxes, current_bill)"); we evaluate
them over whitelisted AST nodes and functions only. No attribute access, imports or calls
to anything outside FUNCS.
"""
from __future__ import annotations

import ast
import math
import operator as op
import statistics
from typing import Any

_BIN = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
        ast.FloorDiv: op.floordiv, ast.Mod: op.mod, ast.Pow: op.pow}
_UNARY = {ast.UAdd: op.pos, ast.USub: op.neg}
_CMP = {ast.Gt: op.gt, ast.GtE: op.ge, ast.Lt: op.lt, ast.LtE: op.le, ast.Eq: op.eq, ast.NotEq: op.ne}

FUNCS = {
    "sum": sum, "min": min, "max": max, "abs": abs, "round": round, "len": len,
    "mean": statistics.fmean, "median": statistics.median,
    "pct": lambda part, whole: part / whole * 100,
    "count_above": lambda xs, t: sum(1 for x in xs if x > t),
    "count_below": lambda xs, t: sum(1 for x in xs if x < t),
    "idx_max": lambda xs: max(range(len(xs)), key=xs.__getitem__),
    "idx_min": lambda xs: min(range(len(xs)), key=xs.__getitem__),
    "sqrt": math.sqrt,
}


class CalcError(ValueError):
    pass


def _ev(node: ast.AST, env: dict[str, Any], funcs: dict[str, Any]):
    if isinstance(node, ast.Expression):
        return _ev(node.body, env, funcs)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise CalcError(f"unknown name '{node.id}'")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _ev(node.left, env, funcs), _ev(node.right, env, funcs)
        if isinstance(node.op, ast.Pow) and abs(right) > 10:
            raise CalcError("exponent too large")
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_ev(node.operand, env, funcs))
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP:
        return _CMP[type(node.ops[0])](_ev(node.left, env, funcs), _ev(node.comparators[0], env, funcs))
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_ev(e, env, funcs) for e in node.elts]
    if isinstance(node, ast.Subscript):
        target = _ev(node.value, env, funcs)
        sl = node.slice
        if isinstance(sl, ast.Slice):
            parts = [None if p is None else _ev(p, env, funcs) for p in (sl.lower, sl.upper, sl.step)]
            return target[slice(*parts)]
        return target[_ev(sl, env, funcs)]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        if node.func.id not in funcs:
            raise CalcError(f"function '{node.func.id}' is not allowed")
        return funcs[node.func.id](*[_ev(a, env, funcs) for a in node.args])
    raise CalcError(f"unsupported syntax: {type(node).__name__}")


def evaluate(expr: str, env: dict[str, Any], extra_funcs: dict[str, Any] | None = None):
    if not isinstance(expr, str) or len(expr) > 400:
        raise CalcError("expression missing or too long")
    funcs = {**FUNCS, **(extra_funcs or {})}
    try:
        value = _ev(ast.parse(expr.strip(), mode="eval"), env, funcs)
    except CalcError:
        raise
    except (SyntaxError, ZeroDivisionError, TypeError, ValueError, IndexError, KeyError,
            OverflowError, RecursionError, statistics.StatisticsError) as exc:
        raise CalcError(f"{type(exc).__name__}: {exc}") from exc
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CalcError("calculation produced a non-finite number")
        value = round(value, 4)
    return value
