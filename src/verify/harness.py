"""Differential-testing harness (implementation.md 6.2, 6.3).

Works for any program that follows the batch convention used by the corpus:
``ACCEPT <record>`` reads one fixed-width input record, and the program prints
``DISPLAY "NAME=" NAME`` for each output. Layout, outputs and value domains are
read from the parsed program itself, so no generator spec is needed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from src.analysis.analyze import Analysis
from src.analysis.ast import OtherStmt, PerformStmt, Stmt
from src.analysis.dictionary import DataItem
from src.verify.engine import base, is_numeric_entry, store, to_num


@dataclass
class Harness:
    record: str
    layout: list[dict[str, Any]]                  # elementary fields in record order
    outputs: list[str]                            # displayed field names
    fields: dict[str, dict[str, Any]]             # dictionary entries (COBOL name -> entry)
    initial: dict[str, Any] = field(default_factory=dict)   # VALUE clauses of outputs/work fields

    @property
    def record_length(self) -> int:
        return sum(f["length"] for f in self.layout)


def _walk(stmts: list[Stmt]):
    for s in stmts:
        yield s
        if isinstance(s, PerformStmt) and s.body:
            yield from _walk(s.body)


def _elementary(item: DataItem) -> list[DataItem]:
    out: list[DataItem] = []
    for c in item.children:
        if c.level == 88 or c.redefines:
            continue
        if c.pic is None and c.children:
            out.extend(_elementary(c))
        elif c.pic:
            out.append(c)
    return out


def build_harness(analysis: Analysis) -> Optional[Harness]:
    """Harness for programs that ACCEPT one record and DISPLAY NAME=VALUE pairs; else None."""
    program = analysis.program
    record_name: Optional[str] = None
    outputs: list[str] = []
    for para in program.paragraphs:
        for s in _walk(para.statements):
            if isinstance(s, OtherStmt) and s.verb == "ACCEPT" and record_name is None:
                parts = s.text.split()
                if len(parts) == 2:
                    record_name = parts[1]
            if isinstance(s, OtherStmt) and s.verb == "DISPLAY":
                parts = s.text.split()
                if len(parts) == 3 and parts[1].startswith("'") and parts[1].endswith("='"):
                    outputs.append(parts[2])
    if not record_name or not outputs:
        return None
    d = program.dictionary
    rec = d.lookup(record_name)
    if rec is None:
        return None
    items = _elementary(rec) if rec.children else ([rec] if rec.pic else [])
    if not items:
        return None
    layout = []
    for it in items:
        info = it.picinfo
        if it.usage not in ("DISPLAY",) or info is None:
            return None             # binary/packed input fields: not supported by the text harness
        layout.append({"name": it.name, "pic": it.pic, "length": info.length, "entry": it.entry()})
    fields = d.entries()
    initial = {}
    for name, entry in fields.items():
        if "value" in entry:
            initial[name] = store(entry["value"] if entry["value"] != " " else "", entry)
        elif is_numeric_entry(entry):
            initial[name] = store(0, entry)
        else:
            initial[name] = ""
    return Harness(record_name, layout, outputs, fields, initial)


# ---- encode / decode -----------------------------------------------------------------

def encode_value(value: Any, entry: dict[str, Any], length: int) -> str:
    if is_numeric_entry(entry):
        n = store(value, entry)
        scale = int(entry.get("scale") or 0)
        digits = int(n.scaleb(scale).to_integral_value()) if isinstance(n, Decimal) else 0
        return str(abs(digits)).rjust(length, "0")[-length:]
    return str(value).ljust(length)[:length]


def encode_record(h: Harness, row: dict[str, Any]) -> str:
    out = []
    for f in h.layout:
        value = row.get(f["name"], 0 if is_numeric_entry(f["entry"]) else "")
        out.append(encode_value(value, f["entry"], f["length"]))
    return "".join(out)


def decode_display(text: str, entry: Optional[dict[str, Any]]) -> Any:
    if is_numeric_entry(entry):
        t = text.strip()
        neg = t.startswith("-") or t.endswith("-")
        digits = "".join(ch for ch in t if ch.isdigit())
        if not digits:
            return Decimal(0)
        scale = int(entry.get("scale") or 0)
        if "." in t:
            n = Decimal(t.replace("+", "").replace("-", ""))
        else:
            n = Decimal(int(digits)).scaleb(-scale)
        return -n if neg else n
    return text.rstrip()


def same_output(expected: Any, actual: Any, entry: Optional[dict[str, Any]]) -> bool:
    """Equal outputs; numbers may differ by one unit in the last place (ROUNDED vs truncation)."""
    if is_numeric_entry(entry):
        a, b = to_num(expected), to_num(actual)
        if a is None or b is None:
            return False
        unit = Decimal(1).scaleb(-int(entry.get("scale") or 0))
        return abs(a - b) <= unit
    return str(expected).rstrip() == str(actual).rstrip()


# ---- input grids -----------------------------------------------------------------------

def _leaf_values(c: dict[str, Any], out: dict[str, set]) -> None:
    for k in ("all", "any"):
        for x in c.get(k) or []:
            _leaf_values(x, out)
    inner = c.get("not") or c.get("not_")
    if inner:
        _leaf_values(inner, out)
    if c.get("source_name") and "value" in c and c.get("value") is not None:
        vals = c["value"] if isinstance(c["value"], list) else [c["value"]]
        out.setdefault(base(c["source_name"]), set()).update(map(_hashable, vals))


def _hashable(v: Any) -> Any:
    return str(v) if not isinstance(v, (int, float, str)) else v


def candidates(h: Harness, conditions: list[dict[str, Any]], rng: random.Random) -> dict[str, list[Any]]:
    """Boundary values per input field: t-1, t, t+1 around every threshold, plus extremes and noise."""
    lits: dict[str, set] = {}
    for c in conditions:
        _leaf_values(c, lits)
    cands: dict[str, list[Any]] = {}
    for f in h.layout:
        entry, name = f["entry"], f["name"]
        if is_numeric_entry(entry):
            scale = int(entry.get("scale") or 0)
            unit = Decimal(1).scaleb(-scale)
            top = Decimal(10) ** int(entry.get("int_digits") or 1) - unit
            vals = {Decimal(0), top, unit}
            for t in lits.get(name, set()):
                n = to_num(t)
                if n is not None:
                    vals.update({n - unit, n, n + unit})
            for _ in range(3):
                vals.add(store(Decimal(rng.random()) * min(top, Decimal(100000)), entry))
            cands[name] = sorted(v for v in vals if Decimal(0) <= v <= top)
        else:
            vals = {str(t) for t in lits.get(name, set())} | {"Z" * min(f["length"], 1), ""}
            cands[name] = sorted(vals)
    return cands


def build_grid(h: Harness, conditions: list[dict[str, Any]], seed: int = 7, random_rows: int = 40,
               targeted_tries: int = 300, max_rows: int = 250) -> list[dict[str, Any]]:
    """Rows that make each condition true and false at least once when possible, plus random rows."""
    from src.verify.engine import eval_cond

    rng = random.Random(seed)
    cands = candidates(h, conditions, rng)
    names = [f["name"] for f in h.layout]

    def sample() -> dict[str, Any]:
        return {n: rng.choice(cands[n]) for n in names}

    rows: list[dict[str, Any]] = [{n: cands[n][0] for n in names}]
    for c in conditions:
        for want in (True, False):
            for _ in range(targeted_tries):
                row = sample()
                if eval_cond(c, {**h.initial, **row}) == want:
                    rows.append(row)
                    # walk each field across its boundary values to probe thresholds
                    for n in names:
                        for v in cands[n]:
                            if len(cands[n]) <= 12:
                                rows.append({**row, n: v})
                    break
    rows.extend(sample() for _ in range(random_rows))
    seen, unique = set(), []
    for r in rows:
        key = tuple(str(r[n]) for n in names)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    if len(unique) > max_rows:          # keep the targeted rows first, sample the rest
        head = unique[: max_rows // 2]
        unique = head + rng.sample(unique[max_rows // 2:], max_rows - len(head))
    return unique
