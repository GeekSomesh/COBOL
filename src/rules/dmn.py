"""DMN 1.3 export: one decision table per decision block (rules.md section 7).

Each rule set (``export.rule_sets``: a first-match chain or a nested IF) becomes a
``decision`` with a ``decisionTable`` whose hit policy is FIRST, rows in source
order, so first-match semantics carry over exactly. A rule's condition (with its
callers' context) is put in negation normal form and expanded into disjunctive
normal form; each conjunction is one row, each field one input column with a
FEEL unary test (``>= 740``, ``[1..15]``, ``"Y"``, ``not("N")``, or ``? > other``
for comparisons between fields). ``set`` / ``compute`` actions fill output
columns (computed values as FEEL arithmetic over business names); PERFORM, CALL
and GO TO go to an annotation column together with the rule id and source lines.
An IF with an ELSE gives a second group of rows for the negated condition.

Sets that cannot be expressed (class tests such as IS NUMERIC, more than
``MAX_ROWS`` rows after expansion) are skipped with a reason, like PMML.
"""

from __future__ import annotations

import re
from itertools import product
from typing import Any, Optional
from xml.sax.saxutils import escape, quoteattr

from src.common.models import Rule
from src.rules.canon import _expand, _nnf
from src.rules.export import rule_sets

NS = "https://www.omg.org/spec/DMN/20191111/MODEL/"
MAX_ROWS = 64


class NotExpressible(ValueError):
    pass


def _plain(c: Any) -> dict[str, Any]:
    return c if isinstance(c, dict) else c.model_dump(by_alias=True, exclude_none=True)


def _dnf(c: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """NNF condition -> list of conjunctions (lists of leaves)."""
    if "all" in c:
        parts = [_dnf(x) for x in c["all"]] or [[[]]]
        out = [sum(combo, []) for combo in product(*parts)]
    elif "any" in c:
        out = [conj for x in c["any"] for conj in _dnf(x)]
    elif "not" in c:
        leaf = c["not"]
        if "op" not in leaf:
            raise NotExpressible("negated group")
        out = [[{**leaf, "negated": True}]]
    else:
        out = [[c]]
    if len(out) > MAX_ROWS:
        raise NotExpressible(f"more than {MAX_ROWS} rows after expansion")
    return out


def _lit(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    return '"' + str(v).rstrip().replace("\\", "\\\\").replace('"', '\\"') + '"'


def feel_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9_]", "_", str(name).lower()).strip("_") or "field"
    return n if not n[0].isdigit() else "f_" + n


_OPS = {"<": "<", "<=": "<=", ">": ">", ">=": ">="}


def _unary(tests: list[dict[str, Any]], names: dict[str, str]) -> str:
    """FEEL unary test for all constraints one row puts on one input."""
    pieces: list[str] = []
    for t in tests:
        op, neg = t["op"], t.get("negated", False)
        if op.startswith("is_"):
            raise NotExpressible(f"class test {op}")
        if t.get("ref"):
            rhs = names.get(t["ref"].split("(")[0].strip().upper(), feel_name(t["ref"]))
            expr = {"==": f"? = {rhs}", "!=": f"? != {rhs}"}.get(op) or f"? {_OPS[op]} {rhs}"
            pieces.append(f"not({expr})" if neg else expr)
            continue
        v = t.get("value")
        if op == "==":
            pieces.append(f"not({_lit(v)})" if neg else _lit(v))
        elif op == "!=":
            pieces.append(_lit(v) if neg else f"not({_lit(v)})")
        elif op in _OPS:
            flip = {"<": ">=", "<=": ">", ">": "<=", ">=": "<"}
            pieces.append(f"{flip[op] if neg else _OPS[op]} {_lit(v)}")
        else:
            raise NotExpressible(f"operator {op}")
    if len(pieces) == 1:
        return pieces[0]
    lows = [p for p in pieces if p.startswith((">=", ">")) and not p.startswith(">=?")]
    highs = [p for p in pieces if p.startswith(("<=", "<"))]
    if len(lows) == 1 and len(highs) == 1 and len(pieces) == 2:          # a range: [a..b), (a..b]
        lo, hi = lows[0], highs[0]
        a, b = lo.split(" ", 1)[1], hi.split(" ", 1)[1]
        return ("[" if lo.startswith(">=") else "(") + f"{a}..{b}" + ("]" if hi.startswith("<=") else ")")
    as_expr = []
    for p in pieces:                                   # general form: a boolean expression over ?
        if p.startswith("?") or p.startswith("not(?"):
            as_expr.append(p)
        elif p.startswith("not("):
            as_expr.append(f"? != {p[4:-1]}")
        elif p.split(" ", 1)[0] in ("<", "<=", ">", ">="):
            as_expr.append(f"? {p}")
        else:
            as_expr.append(f"? = {p}")
    return " and ".join(as_expr)


def _expr(expr: str, names: dict[str, str]) -> str:
    def rep(m: re.Match) -> str:
        word = m.group(0)
        if re.fullmatch(r"\d+(\.\d+)?", word):
            return word
        return names.get(word.split("(")[0].upper(), feel_name(word))
    return re.sub(r"[A-Za-z][A-Za-z0-9-]*(?:\([^)]*\))?|\d+(?:\.\d+)?", rep, expr)


def _decision(key: str, rules: list[Rule], n: int) -> str:
    names: dict[str, str] = {}
    types: dict[str, set] = {}
    rows: list[tuple[Rule, list[dict[str, Any]], list[Any], str]] = []
    for r in rules:
        d = r.model_dump(by_alias=True, exclude_none=True)
        cond = d["conditions"] if not d.get("context") else {"all": [d["context"], d["conditions"]]}
        for branch, acts in (("then", d["actions"]), ("else", d["else_actions"])):
            if branch == "else" and not acts:
                continue
            c = _nnf(_expand(cond if branch == "then" else {"not": cond}))
            for conj in _dnf(c):
                rows.append((r, conj, acts, branch))
                for leaf in conj:
                    src = leaf["source_name"].split("(")[0].strip().upper()
                    names.setdefault(src, feel_name(leaf.get("field") or src))
                    types.setdefault(src, set()).add("number" if isinstance(leaf.get("value"), (int, float))
                                                     and not isinstance(leaf.get("value"), bool) else "string")
                    if leaf.get("ref"):
                        ref = leaf["ref"].split("(")[0].strip().upper()
                        names.setdefault(ref, feel_name(ref))
        for a in d["actions"] + d["else_actions"]:
            if a["type"] in ("set", "compute") and a.get("source_name"):
                names.setdefault(a["source_name"].split("(")[0].strip().upper(),
                                 feel_name(a.get("target") or a["source_name"]))
    if len(rows) > MAX_ROWS:
        raise NotExpressible(f"more than {MAX_ROWS} rows")
    inputs = [k for k in dict.fromkeys(src for _, conj, _, _ in rows for src in
                                        (l["source_name"].split("(")[0].strip().upper() for l in conj))]
    outputs = list(dict.fromkeys(a["source_name"].split("(")[0].strip().upper()
                                 for _, _, acts, _ in rows for a in acts if a["type"] in ("set", "compute")))
    pid = f"{n}"
    x = [f'  <decision id="decision_{pid}" name={quoteattr(key)}>',
         f'    <decisionTable id="table_{pid}" hitPolicy="FIRST">']
    for i, src in enumerate(inputs):
        typ = "number" if types.get(src) == {"number"} else "string"
        x += [f'      <input id="in_{pid}_{i}" label={quoteattr(names[src])}>',
              f'        <inputExpression id="inx_{pid}_{i}" typeRef="{typ}"><text>{escape(names[src])}</text></inputExpression>',
              "      </input>"]
    for j, out in enumerate(outputs or ["no_output"]):
        x.append(f'      <output id="out_{pid}_{j}" name={quoteattr(names.get(out, feel_name(out)))}/>')
    x.append('      <annotation name="source"/>')
    for k, (r, conj, acts, branch) in enumerate(rows):
        x.append(f'      <rule id="rule_{pid}_{k}">')
        if r.title:
            x.append(f"        <description>{escape(r.title)}</description>")
        for src in inputs:
            tests = [l for l in conj if l["source_name"].split("(")[0].strip().upper() == src]
            x.append(f"        <inputEntry><text>{escape(_unary(tests, names) if tests else '-')}</text></inputEntry>")
        for out in outputs or ["no_output"]:
            val = "null"
            for a in acts:
                if a["type"] in ("set", "compute") and a.get("source_name", "").split("(")[0].strip().upper() == out:
                    if a.get("expr"):
                        val = _expr(a["expr"], names)
                    else:
                        val = _lit(a.get("value"))
            x.append(f"        <outputEntry><text>{escape(val)}</text></outputEntry>")
        other = "; ".join(f"{a['type']} {a.get('source_name', '')}".strip() for a in acts
                          if a["type"] not in ("set", "compute"))
        note = f"{r.rule_id} ({branch}) lines {r.trace.line_start}-{r.trace.line_end}" + (f"; {other}" if other else "")
        x += [f"        <annotationEntry><text>{escape(note)}</text></annotationEntry>", "      </rule>"]
    x += ["    </decisionTable>", "  </decision>"]
    return "\n".join(x)


def to_dmn(rules: list[Rule], name: str = "cobol-rules") -> tuple[str, list[str]]:
    """DMN 1.3 XML with one decision table per rule set; returns (xml, skipped reasons)."""
    parts, skipped = [], []
    for n, (key, group) in enumerate(rule_sets(rules).items(), start=1):
        try:
            parts.append(_decision(key, group, n))
        except NotExpressible as exc:
            skipped.append(f"{key}: {exc}")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<definitions xmlns="{NS}" id="definitions_1" name={quoteattr(name)} '
           f'namespace="urn:cobol-rules:{escape(feel_name(name))}">\n' + "\n".join(parts) + "\n</definitions>\n")
    return xml, skipped
