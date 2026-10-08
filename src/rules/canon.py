"""Canonical forms so equivalent rules compare equal (rules.md 3, evaluation.md 2).

Conditions are keyed on COBOL source names (business names are the LLM's job and
are scored separately). Normalisation:

* ``in`` -> ``any`` of ``==``; ``not_in`` -> ``all`` of ``!=``; ``between`` -> ``>=`` and ``<=``
* negation pushed to the leaves (NNF), flipping relational operators
* nested ``all``/``any`` flattened, single-child groups unwrapped, children sorted
* numbers compared as decimals (5000 == 5000.00), strings right-trimmed (COBOL padding)
* expressions upper-cased with whitespace removed
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Union

from pydantic import BaseModel

NEGATE = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}
Key = Union[tuple, str]


def _plain(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", by_alias=True, exclude_none=True)
    return obj


def value_key(v: Any) -> str:
    if isinstance(v, bool):
        return f"s:{v}"
    if isinstance(v, (int, float)):
        return "n:" + _dec(v)
    if isinstance(v, str):
        try:
            if v.strip() and v.strip().lstrip("+-").replace(".", "", 1).isdigit():
                return "n:" + _dec(v.strip())
        except InvalidOperation:
            pass
        return "s:" + v.rstrip()
    return "?:" + repr(v)


def _dec(v: Any) -> str:
    d = Decimal(str(v)).normalize()
    return format(d, "f")


def _name(leaf: dict[str, Any]) -> str:
    return str(leaf.get("source_name") or leaf.get("field", "")).upper().strip()


def _expand(c: dict[str, Any]) -> dict[str, Any]:
    """Rewrite set/range operators into plain relations."""
    if "all" in c:
        return {"all": [_expand(x) for x in c["all"]]}
    if "any" in c:
        return {"any": [_expand(x) for x in c["any"]]}
    if "not" in c:
        return {"not": _expand(c["not"])}
    op = c["op"]
    if op in ("in", "not_in") and isinstance(c.get("value"), list):
        rel = "==" if op == "in" else "!="
        parts = [dict(c, op=rel, value=v) for v in c["value"]]
        return {"any": parts} if op == "in" else {"all": parts}
    if op == "between" and isinstance(c.get("value"), list) and len(c["value"]) == 2:
        lo, hi = c["value"]
        return {"all": [dict(c, op=">=", value=lo), dict(c, op="<=", value=hi)]}
    return c


def _nnf(c: dict[str, Any], neg: bool = False) -> dict[str, Any]:
    if "not" in c:
        return _nnf(c["not"], not neg)
    if "all" in c or "any" in c:
        kind = "all" if "all" in c else "any"
        if neg:
            kind = "any" if kind == "all" else "all"
        items = c.get("all") if "all" in c else c.get("any")
        return {kind: [_nnf(x, neg) for x in items]}
    if neg:
        if c["op"] in NEGATE:
            return dict(c, op=NEGATE[c["op"]])
        return {"not": c}
    return c


def _key(c: dict[str, Any]) -> Key:
    if "all" in c or "any" in c:
        kind = "all" if "all" in c else "any"
        children: list[Key] = []
        for x in (c["all"] if kind == "all" else c["any"]):
            k = _key(x)
            if isinstance(k, tuple) and k and k[0] == kind:
                children.extend(k[1])              # flatten same-kind nesting
            else:
                children.append(k)
        uniq = sorted(set(children), key=repr)
        if len(uniq) == 1:
            return uniq[0]
        if kind == "all" and not uniq:
            return ("true",)
        return (kind, tuple(uniq))
    if "not" in c:
        return ("not", _key(c["not"]))
    rhs = "ref:" + str(c["ref"]).upper() if c.get("ref") else value_key(c.get("value")) if "value" in c else ""
    return f"{_name(c)} {c['op']} {rhs}"


def canon_cond(cond: Any) -> Key:
    return _key(_nnf(_expand(_plain(cond))))


def canon_expr(expr: str) -> str:
    return "".join(str(expr).upper().split())


def canon_action(a: Any) -> tuple:
    a = _plain(a)
    if a.get("expr"):
        rhs = "expr:" + canon_expr(a["expr"])
    elif "value" in a:
        rhs = value_key(a["value"])
    else:
        rhs = ""
    return (a["type"], str(a.get("source_name") or a.get("target") or "").upper(), rhs)


def canon_actions(actions: list[Any]) -> tuple:
    return tuple(sorted(canon_action(a) for a in actions))


def rule_key(rule: Any) -> tuple:
    r = _plain(rule)
    return (canon_cond(r["conditions"]), canon_actions(r.get("actions", [])), canon_actions(r.get("else_actions", [])))
