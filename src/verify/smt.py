"""Exact reasoning about rule conditions with the z3 SMT solver.

Sampling (the grid in harness.py, random rows in decide.py) can miss the one
input where two conditions differ or two rules collide. Here a condition tree is
translated into a z3 formula with the same meaning as ``engine.eval_cond``:

* numeric fields are reals; arithmetic in a condition (``TXN-AMT + TXN-FEE``) is
  real arithmetic, division by zero gives 0 like the evaluator;
* alphanumeric fields are strings compared without trailing spaces (COBOL pads
  with spaces), ``IS NUMERIC`` / ``IS ALPHABETIC`` are regular-expression tests;
* a field compared with a number anywhere is numeric everywhere (or given by the
  data dictionary through ``numeric``).

``equivalent`` proves two conditions equal or returns an input where they differ;
``witness`` returns an input satisfying all given conditions, or None when no such
input exists. Anything outside this fragment raises ``Unsupported`` so callers
fall back to sampling. Every check is bounded by a timeout (``TIMEOUT_MS``).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Iterable, Optional

try:
    import z3
except ImportError:                     # optional dependency: callers fall back to sampling
    z3 = None

TIMEOUT_MS = 2000
_TOKEN = re.compile(r"\s*(\*\*|[-+*/()]|\d+(?:\.\d+)?|\.\d+|[A-Za-z0-9][A-Za-z0-9_-]*(?:\([^)]*\))?)")
_EXPR_HINT = re.compile(r"\s[-+*/]\s|\*\*|^\(")


class Unsupported(ValueError):
    pass


def available() -> bool:
    return z3 is not None


def _num(v: Any) -> Optional[Decimal]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    return None


def _norm(name: str) -> str:
    return "".join(str(name).upper().split())


def _is_expr(name: str) -> bool:
    return bool(_EXPR_HINT.search(str(name)))


def _expr_names(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text):
        if re.match(r"[A-Za-z]", t) or ("(" in t and not t.startswith("(")):
            out.append(_norm(t))
    return out


def _leaves(c: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for k in ("all", "any"):
        for x in c.get(k) or []:
            yield from _leaves(x)
    inner = c.get("not") if c.get("not") is not None else c.get("not_")
    if inner is not None:
        yield from _leaves(inner)
    if c.get("source_name"):
        yield c


class _Encoder:
    """Shared variables (one per field) for a set of conditions."""

    def __init__(self, conds: list[dict[str, Any]], numeric: Optional[set[str]] = None):
        if z3 is None:
            raise Unsupported("z3 is not installed")
        self.numeric = {_norm(n) for n in (numeric or set())}
        self.text: set[str] = set()
        for c in conds:
            self._infer(c)
        changed = True
        while changed:                           # a field compared with a numeric field is numeric
            changed = False
            for c in conds:
                for leaf in _leaves(c):
                    a, b = _norm(leaf["source_name"]), _norm(leaf.get("ref") or "")
                    if b and (a in self.numeric) != (b in self.numeric):
                        self.numeric.update({a, b})
                        changed = True
        self.text -= self.numeric
        self.vars: dict[str, Any] = {}
        self.axioms: list[Any] = []

    def _infer(self, c: dict[str, Any]) -> None:
        for leaf in _leaves(c):
            names = [leaf["source_name"]] + ([leaf["ref"]] if leaf.get("ref") else [])
            for name in names:
                if _is_expr(name):
                    self.numeric.update(_expr_names(name))
            vals = leaf.get("value")
            vals = vals if isinstance(vals, list) else [vals]
            key = _norm(leaf["source_name"])
            if leaf["op"] in ("is_positive", "is_negative", "is_zero"):
                self.numeric.add(key)
            elif any(_num(v) is not None for v in vals):
                self.numeric.add(key)
            elif any(isinstance(v, str) for v in vals) or leaf["op"] in ("is_alphabetic",):
                self.text.add(key)

    # ---- variables ---------------------------------------------------------------
    def var(self, name: str) -> Any:
        key = _norm(name)
        if key not in self.vars:
            if key in self.numeric:
                self.vars[key] = z3.Real(key)
            else:
                v = z3.String(key)
                self.vars[key] = v
                n = z3.Length(v)
                self.axioms.append(z3.Or(n == 0, z3.SubString(v, n - 1, 1) != z3.StringVal(" ")))
                self.axioms.append(z3.InRe(v, z3.Star(z3.Range(" ", "~"))))   # printable display text
        return self.vars[key]

    def operand(self, name: str) -> Any:
        if _is_expr(name):
            return self.arith(name)
        return self.var(name)

    def arith(self, text: str) -> Any:
        toks = _TOKEN.findall(text.strip())
        if "".join(t.strip() for t in toks) != "".join(text.split()):
            raise Unsupported(f"cannot read expression {text!r}")
        pos = 0

        def peek():
            return toks[pos] if pos < len(toks) else None

        def take():
            nonlocal pos
            pos += 1
            return toks[pos - 1]

        def atom():
            t = take()
            if t == "(":
                v = add()
                if take() != ")":
                    raise Unsupported("missing )")
                return v
            if t in ("+", "-"):
                v = atom()
                return v if t == "+" else -v
            if re.fullmatch(r"\d+(\.\d+)?|\.\d+", t):
                return z3.RealVal(t)
            key = _norm(t)
            self.numeric.add(key)
            if key in self.vars and not z3.is_arith(self.vars[key]):
                raise Unsupported(f"{t} used as text and number")
            return self.var(key)

        def power():
            v = atom()
            if peek() == "**":
                take()
                e = power()
                if not (z3.is_rational_value(e) and e.as_fraction().denominator == 1 and 0 <= e.as_long() <= 8):
                    raise Unsupported("non-constant exponent")
                out = z3.RealVal(1)
                for _ in range(e.as_long()):
                    out = out * v
                return out
            return v

        def mul():
            v = power()
            while peek() in ("*", "/"):
                op = take()
                r = power()
                v = v * r if op == "*" else z3.If(r == 0, z3.RealVal(0), v / r)
            return v

        def add():
            v = mul()
            while peek() in ("+", "-"):
                op = take()
                r = mul()
                v = v + r if op == "+" else v - r
            return v

        out = add()
        if pos != len(toks):
            raise Unsupported(f"trailing tokens in {text!r}")
        return out

    # ---- conditions ----------------------------------------------------------------
    def cond(self, c: dict[str, Any]) -> Any:
        if c.get("all") is not None:
            return z3.And([self.cond(x) for x in c["all"]]) if c["all"] else z3.BoolVal(True)
        if c.get("any") is not None:
            return z3.Or([self.cond(x) for x in c["any"]]) if c["any"] else z3.BoolVal(False)
        inner = c.get("not") if c.get("not") is not None else c.get("not_")
        if inner is not None:
            return z3.Not(self.cond(inner))
        return self.leaf(c)

    def _const(self, v: Any, numeric: bool) -> Any:
        if numeric:
            n = _num(v)
            if n is None and isinstance(v, str):
                try:
                    n = Decimal(v.strip())
                except ArithmeticError:
                    n = None
            if n is None:
                return None
            return z3.RealVal(str(n))
        if _num(v) is not None:
            return None
        return z3.StringVal(str(v).rstrip())

    def compare(self, op: str, a: Any, b: Any) -> Any:
        if b is None:                                   # numeric field vs non-numeric text, or the reverse
            if op in ("==", "!="):
                return z3.BoolVal(op == "!=")
            raise Unsupported("ordering between a number and text")
        if z3.is_arith(a) != z3.is_arith(b):
            raise Unsupported("number compared with text")
        ops = {"==": lambda: a == b, "!=": lambda: a != b, "<": lambda: a < b, "<=": lambda: a <= b,
               ">": lambda: a > b, ">=": lambda: a >= b}
        if op not in ops:
            raise Unsupported(f"operator {op}")
        return ops[op]()                                # strings: z3's lexicographic str.< / str.<=

    def leaf(self, c: dict[str, Any]) -> Any:
        name, op = c["source_name"], c["op"]
        x = self.operand(name)
        numeric = z3.is_arith(x)
        if op == "is_numeric":
            if numeric:
                return z3.BoolVal(True)
            return z3.InRe(x, z3.Plus(z3.Range("0", "9")))
        if op == "is_alphabetic":
            if numeric:
                return z3.BoolVal(False)
            letters = z3.Union(z3.Range("a", "z"), z3.Range("A", "Z"), z3.Re(" "))
            return z3.InRe(x, z3.Star(letters))
        if op in ("is_positive", "is_negative", "is_zero"):
            if not numeric:
                raise Unsupported(f"{op} on text")
            return {"is_positive": x > 0, "is_negative": x < 0, "is_zero": x == 0}[op]
        if c.get("ref"):
            y = self.operand(c["ref"])
            return self.compare(op, x, y)
        v = c.get("value")
        if op == "between":
            lo, hi = (self._const(t, numeric) for t in v)
            return z3.And(self.compare(">=", x, lo), self.compare("<=", x, hi))
        if op in ("in", "not_in"):
            parts = [self.compare("==", x, self._const(t, numeric)) for t in v]
            return z3.Or(parts) if op == "in" else z3.Not(z3.Or(parts))
        return self.compare(op, x, self._const(v, numeric))

    # ---- solving -------------------------------------------------------------------
    def solver(self) -> Any:
        s = z3.Solver()
        s.set("timeout", TIMEOUT_MS)
        return s

    def model(self, m: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, v in self.vars.items():
            val = m.eval(v, model_completion=True)
            if z3.is_arith(v):
                frac = val.as_fraction() if z3.is_rational_value(val) else None
                if frac is None:
                    out[key] = float(val.as_decimal(6).rstrip("?"))
                else:
                    d = Decimal(frac.numerator) / Decimal(frac.denominator)
                    out[key] = int(d) if d == d.to_integral_value() else float(round(d, 6))
            else:
                out[key] = val.as_string()
        return out


def equivalent(a: dict[str, Any], b: dict[str, Any], numeric: Optional[set[str]] = None
               ) -> tuple[Optional[bool], Optional[dict[str, Any]]]:
    """(True, None) when proven equal; (False, input where they differ); (None, None) on timeout."""
    enc = _Encoder([a, b], numeric)
    fa, fb = enc.cond(a), enc.cond(b)
    s = enc.solver()
    s.add(*enc.axioms)
    s.add(fa != fb)
    r = s.check()
    if r == z3.unsat:
        return True, None
    if r == z3.sat:
        return False, enc.model(s.model())
    return None, None


def witness(conds: list[dict[str, Any]], numeric: Optional[set[str]] = None) -> tuple[Optional[bool], Optional[dict[str, Any]]]:
    """(True, input satisfying every condition); (False, None) when none exists; (None, None) on timeout."""
    enc = _Encoder(conds, numeric)
    fs = [enc.cond(c) for c in conds]
    s = enc.solver()
    s.add(*enc.axioms)
    s.add(*fs)
    r = s.check()
    if r == z3.sat:
        return True, enc.model(s.model())
    if r == z3.unsat:
        return False, None
    return None, None
