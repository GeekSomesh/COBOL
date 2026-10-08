"""Rule evaluator with COBOL-like data semantics (implementation.md 6.1).

Values are ``Decimal`` for numeric fields and right-trimmed strings otherwise.
Assignments are stored the way COBOL stores them: numeric results are truncated
to the target's PIC scale and integer digits (no ROUNDED, no SIZE ERROR), text is
cut to the field length. Expressions are evaluated by a small parser, never
``eval``.
"""

from __future__ import annotations

import re
from decimal import ROUND_DOWN, Decimal, InvalidOperation, getcontext
from typing import Any, Iterable, Optional

getcontext().prec = 40
Num = Decimal


def base(name: str) -> str:
    return str(name).split("(")[0].strip().upper()


def is_numeric_entry(entry: Optional[dict[str, Any]]) -> bool:
    return bool(entry) and entry.get("category") in ("numeric", "numeric-edited")


def to_num(v: Any) -> Optional[Decimal]:
    if isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str):
        t = v.strip()
        if t == "":
            return Decimal(0)
        try:
            return Decimal(t)
        except InvalidOperation:
            return None
    return None


def store(value: Any, entry: Optional[dict[str, Any]]) -> Any:
    """Value as COBOL would hold it after a MOVE/COMPUTE into a field with this PIC."""
    if is_numeric_entry(entry):
        n = to_num(value)
        if n is None:
            n = Decimal(0)
        scale = int(entry.get("scale") or 0)
        digits = int(entry.get("int_digits") or 18)
        n = n.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_DOWN)
        limit = Decimal(10) ** digits
        sign = -1 if n < 0 else 1
        n = abs(n) % limit
        if entry.get("signed"):
            n = n * sign
        return n
    text = "" if value is None else str(value)
    if isinstance(value, Decimal):
        text = format(value, "f")
    length = entry.get("length") if entry else None
    if length:
        text = text[:length]
    return text.rstrip()


def _cmp_values(a: Any, b: Any) -> tuple[Any, Any]:
    na, nb = to_num(a), to_num(b)
    if na is not None and nb is not None and not (isinstance(a, str) and isinstance(b, str)):
        return na, nb
    return str(a if not isinstance(a, Decimal) else format(a, "f")).rstrip(), \
        str(b if not isinstance(b, Decimal) else format(b, "f")).rstrip()


def compare(op: str, a: Any, b: Any) -> bool:
    x, y = _cmp_values(a, b)
    if op == "==":
        return x == y
    if op == "!=":
        return x != y
    try:
        if op == "<":
            return x < y
        if op == "<=":
            return x <= y
        if op == ">":
            return x > y
        if op == ">=":
            return x >= y
    except TypeError:
        return False
    raise ValueError(f"unknown operator {op}")


def eval_cond(c: dict[str, Any], row: dict[str, Any]) -> bool:
    if c.get("all") is not None:
        return all(eval_cond(x, row) for x in c["all"])
    if c.get("any") is not None:
        return any(eval_cond(x, row) for x in c["any"])
    if c.get("not") is not None or c.get("not_") is not None:
        return not eval_cond(c.get("not") or c.get("not_"), row)
    v = row.get(base(c["source_name"]))
    op = c["op"]
    rhs = row.get(base(c["ref"])) if c.get("ref") else c.get("value")
    if op == "between":
        return compare(">=", v, rhs[0]) and compare("<=", v, rhs[1])
    if op == "in":
        return any(compare("==", v, x) for x in rhs)
    if op == "not_in":
        return all(compare("!=", v, x) for x in rhs)
    if op == "is_numeric":
        return isinstance(v, Decimal) or (isinstance(v, str) and v != "" and v.strip().isdigit())
    if op == "is_alphabetic":
        return isinstance(v, str) and all(ch.isalpha() or ch == " " for ch in v)
    if op in ("is_positive", "is_negative", "is_zero"):
        n = to_num(v) or Decimal(0)
        return {"is_positive": n > 0, "is_negative": n < 0, "is_zero": n == 0}[op]
    return compare(op, v, rhs)


# ---- arithmetic expressions ------------------------------------------------------

_TOKEN = re.compile(r"\s*(\*\*|[-+*/()]|\d+(?:\.\d+)?|\.\d+|[A-Za-z0-9][A-Za-z0-9_-]*(?:\([^)]*\))?)")


class ExprError(ValueError):
    pass


def eval_expr(expr: str, state: dict[str, Any]) -> Decimal:
    toks = []
    pos = 0
    text = expr.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ExprError(f"cannot read expression {expr!r}")
        toks.append(m.group(1))
        pos = m.end()
    i = 0

    def peek() -> Optional[str]:
        return toks[i] if i < len(toks) else None

    def take() -> str:
        nonlocal i
        i += 1
        return toks[i - 1]

    def atom() -> Decimal:
        t = take()
        if t == "(":
            v = add()
            if take() != ")":
                raise ExprError("missing )")
            return v
        if t in "+-":
            v = atom()
            return v if t == "+" else -v
        n = to_num(t)
        if n is not None and re.fullmatch(r"\d+(\.\d+)?|\.\d+", t):
            return n
        if base(t) in state:
            n = to_num(state[base(t)])
            return n if n is not None else Decimal(0)
        raise ExprError(f"unknown identifier {t}")

    def power() -> Decimal:
        v = atom()
        if peek() == "**":
            take()
            return v ** int(power())
        return v

    def mul() -> Decimal:
        v = power()
        while peek() in ("*", "/"):
            op = take()
            r = power()
            v = v * r if op == "*" else (v / r if r != 0 else Decimal(0))
        return v

    def add() -> Decimal:
        v = mul()
        while peek() in ("+", "-"):
            op = take()
            r = mul()
            v = v + r if op == "+" else v - r
        return v

    result = add()
    if i != len(toks):
        raise ExprError(f"trailing tokens in {expr!r}")
    return result


# ---- running rules -----------------------------------------------------------------

def apply_actions(actions: Iterable[dict[str, Any]], state: dict[str, Any], fields: dict[str, dict]) -> None:
    for a in actions:
        target = base(a.get("source_name") or "")
        entry = fields.get(target)
        if a["type"] == "set":
            if a.get("expr"):
                src = state.get(base(a["expr"]))
                state[target] = store(src, entry)
            else:
                state[target] = store(a.get("value"), entry)
        elif a["type"] == "compute":
            state[target] = store(eval_expr(a["expr"], state), entry)
        # perform / call: control transfer, nothing to evaluate here


def run_rules(rules: list[dict[str, Any]], state: dict[str, Any], fields: dict[str, dict]) -> list[int]:
    """Apply rules in source order; return the indexes of rules whose condition held."""
    fired: list[int] = []
    for k, r in enumerate(rules):
        if eval_cond(r["conditions"], state):
            fired.append(k)
            apply_actions(r.get("actions", []), state, fields)
        else:
            apply_actions(r.get("else_actions", []), state, fields)
    return fired
