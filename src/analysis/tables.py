"""Decode hard-coded lookup tables (VALUE-initialised storage seen through an OCCURS).

The classic COBOL pattern keeps a table's rows as FILLER literals and lays an
OCCURS over the same bytes with REDEFINES::

    01 RATE-TABLE-VALUES.
       05 FILLER PIC X(9) VALUE 'G00150250'.
       ...
    01 RATE-TABLE REDEFINES RATE-TABLE-VALUES.
       05 RATE-ENTRY OCCURS 3 TIMES.
          10 RATE-TIER PIC X.  10 RATE-PCT PIC 9V999.  10 RATE-MIN-FEE PIC 9(2)V99.

``decode(dictionary, "RATE-ENTRY")`` rebuilds the byte image of the VALUE side and
reads it back through the OCCURS layout, giving rows such as
``{"RATE-TIER": "G", "RATE-PCT": Decimal("0.015"), "RATE-MIN-FEE": Decimal("2.50")}``,
each with the source line its bytes come from. Only USAGE DISPLAY storage is
decoded; anything else (COMP fields, nested OCCURS, signed overpunch, values set
at run time) returns None and the SEARCH stays a review item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from src.analysis.dictionary import DataItem, Dictionary

MAX_ROWS = 64          # larger tables are kept as one rule with the table attached for review


class Undecodable(ValueError):
    pass


@dataclass
class Table:
    name: str                                   # the OCCURS item
    columns: list[str]
    rows: list[dict[str, Any]]
    lines: list[list[int]] = field(default_factory=list)   # per row: VALUE lines its bytes come from
    file: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"table": self.name, "columns": self.columns, "file": self.file,
                "rows": [{k: _plain(v) for k, v in r.items()} for r in self.rows], "lines": self.lines}


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def _length(item: DataItem) -> int:
    if item.level == 88:
        return 0
    if item.usage not in ("DISPLAY",):
        raise Undecodable(f"{item.name}: USAGE {item.usage}")
    kids = [c for c in item.children if c.level != 88]
    if kids:
        n = sum(_length(c) for c in kids if not c.redefines)
    else:
        info = item.picinfo
        if info is None:
            raise Undecodable(f"{item.name}: no PICTURE")
        n = info.length
    return n * (item.occurs or 1)


def _image(item: DataItem, lines: list[int]) -> str:
    """Byte image of VALUE-initialised storage; ``lines`` gets the source line of every byte."""
    kids = [c for c in item.children if c.level != 88 and not c.redefines]
    if kids:
        if item.occurs:
            raise Undecodable(f"{item.name}: OCCURS on the VALUE side")
        return "".join(_image(c, lines) for c in kids)
    n = _length(item)
    v = item.value
    info = item.picinfo
    if v is None:
        raise Undecodable(f"{item.name}: no VALUE")
    if info is not None and info.category == "numeric":
        if info.signed:
            raise Undecodable(f"{item.name}: signed numeric VALUE")
        if isinstance(v, str):
            if v.strip() == "":
                raise Undecodable(f"{item.name}: SPACES in numeric item")
            v = Decimal(v)
        digits = str(int((Decimal(v) * (Decimal(10) ** info.scale)).to_integral_value()))
        text = digits.rjust(n, "0")[-n:]
    else:
        s = str(v)
        if s in ("HIGH-VALUES", "LOW-VALUES", "NULL"):
            raise Undecodable(f"{item.name}: {s}")
        text = "0" * n if v == 0 and not isinstance(v, str) else s.ljust(n)[:n]   # ZERO repeats; literals pad
    lines.extend([item.line] * n)
    return text


def _read(raw: str, item: DataItem) -> Any:
    info = item.picinfo
    if info is not None and info.category == "numeric":
        if not raw.isdigit():
            raise Undecodable(f"{item.name}: non-numeric bytes {raw!r}")
        return Decimal(int(raw)) / (Decimal(10) ** info.scale) if info.scale else Decimal(int(raw))
    return raw.rstrip()


def _offset(item: DataItem, root: DataItem) -> int:
    """Byte offset of ``item`` inside ``root`` (no OCCURS between them)."""
    off = 0
    node = item
    while node is not root:
        parent = node.parent
        if parent is None:
            raise Undecodable(f"{item.name} is not inside {root.name}")
        if parent.occurs and parent is not root:
            raise Undecodable("nested OCCURS")
        for c in parent.children:
            if c is node:
                break
            if c.level != 88 and not c.redefines:
                off += _length(c)
        node = parent
    return off


def decode(dictionary: Dictionary, table: str) -> Optional[Table]:
    """Rows of a VALUE-initialised OCCURS table, or None when it cannot be read statically."""
    occ = dictionary.lookup(table)
    if occ is None or not occ.occurs:
        return None
    try:
        # the level-01 group holding the OCCURS, and the storage it redefines
        top = occ
        while top.parent is not None:
            top = top.parent
        if occ.occurs > MAX_ROWS:
            raise Undecodable("table too large")
        cols = [c for c in occ.children if c.level != 88 and not c.redefines]
        if not cols or any(c.children and [k for k in c.children if k.level != 88] for c in cols):
            raise Undecodable("table entries must be elementary columns")
        lines: list[int] = []
        if top.redefines:
            src = dictionary.lookup(top.redefines)
            if src is None:
                raise Undecodable("redefined item not found")
            image = _image(src, lines)
            base = _offset(occ, top)
        else:
            return None                      # rows filled at run time (or all alike): not a static lookup
        row_len = _length(occ) // occ.occurs
        rows, row_lines = [], []
        for r in range(occ.occurs):
            start = base + r * row_len
            if start + row_len > len(image):
                raise Undecodable("VALUE storage shorter than the table")
            row, pos = {}, start
            for c in cols:
                n = _length(c)
                row[c.name] = _read(image[pos:pos + n], c)
                pos += n
            rows.append(row)
            row_lines.append(sorted(set(lines[start:start + row_len])))
        return Table(occ.name, [c.name for c in cols], rows, row_lines, src.file)
    except (Undecodable, ArithmeticError, ValueError):
        return None


# ---- binding a row into a SEARCH rule ---------------------------------------------------

_FLIP = {"==": "==", "!=": "!=", "<": ">", ">": "<", "<=": ">=", ">=": "<="}


class NotBindable(ValueError):
    pass


def _col(name: Any, table: Table) -> Optional[str]:
    """Column name when ``name`` is a table column reference, e.g. RATE-TIER(RT-IDX)."""
    if not isinstance(name, str):
        return None
    text = name.strip().upper()
    head, _, sub = text.partition("(")
    head = head.strip()
    if head not in table.columns:
        if any(re.search(rf"(?<![A-Z0-9-]){re.escape(c)}(?![A-Z0-9-])", text) for c in table.columns):
            raise NotBindable(f"column inside an expression: {name}")
        return None
    if sub and sub.rstrip(")").strip().isdigit():
        raise NotBindable(f"fixed subscript: {name}")       # RATE-TIER(2) is not the search index
    return head


def bind_cond(c: dict[str, Any], row: dict[str, Any], table: Table) -> Any:
    """The condition with the row's column values substituted: a tree, True or False."""
    from src.verify.engine import eval_cond
    if "all" in c or "any" in c:
        key = "all" if "all" in c else "any"
        parts = [bind_cond(x, row, table) for x in c[key]]
        if key == "all":
            if any(p is False for p in parts):
                return False
            rest = [p for p in parts if p is not True]
            return True if not rest else {"all": rest}
        if any(p is True for p in parts):
            return True
        rest = [p for p in parts if p is not False]
        return False if not rest else {"any": rest}
    if "not" in c:
        inner = bind_cond(c["not"], row, table)
        return (not inner) if isinstance(inner, bool) else {"not": inner}
    lhs, ref = _col(c["source_name"], table), _col(c.get("ref"), table)
    if lhs is None and ref is None:
        return c
    if lhs is not None and c.get("ref") and ref is None:            # column vs field
        if c["op"] not in _FLIP:
            raise NotBindable(f"operator {c['op']} against a field")
        return {"source_name": c["ref"], "op": _FLIP[c["op"]], "value": _plain(row[lhs])}
    if lhs is None:                                                  # field vs column
        return {"source_name": c["source_name"], "op": c["op"], "value": _plain(row[ref])}
    state = {k: (_plain(v) if not isinstance(v, str) else v) for k, v in row.items()}
    return bool(eval_cond(c, state))                                 # column vs literal / column: constant


def bind_actions(acts: list[dict[str, Any]], row: dict[str, Any], table: Table) -> list[dict[str, Any]]:
    out = []
    for a in acts:
        a = dict(a)
        if a["type"] in ("set", "compute"):
            if _col(a["source_name"], table) is not None:
                raise NotBindable("the search writes into the table")
            expr = a.get("expr")
            if expr:
                col = None
                try:
                    col = _col(expr, table)
                except NotBindable:
                    pass
                if col is not None:                                  # MOVE RATE-PCT(IDX) TO WS-RATE
                    a.pop("expr")
                    a["type"], a["value"] = "set", _plain(row[col])
                else:                                                # COMPUTE ... RATE-PCT(IDX) ...
                    def lit(m: re.Match) -> str:
                        v = row[m.group(1)]
                        return f"'{v}'" if isinstance(v, str) else str(_plain(v))
                    names = "|".join(re.escape(c) for c in sorted(table.columns, key=len, reverse=True))
                    a["expr"] = re.sub(rf"(?<![A-Z0-9-])({names})(?:\s*\([^)]*\))?(?![A-Z0-9-])", lit, expr.upper())
        out.append(a)
    return out
