"""Byte-level model of COBOL records, so REDEFINES views and OCCURS elements stay in step.

The rule interpreter keeps one value per field. That is wrong whenever two
fields share bytes: in IBM's SAM2, ``MOVE '0' TO TRAN-UPDATE-NUMX1`` changes the
first digit of ``TRAN-UPDATE-NUM`` because both REDEFINE ``TRAN-UPDATE-DATA``.
``Storage`` lays every record out in bytes (offsets from the PICTUREs, REDEFINES
sharing the redefined item's bytes, OCCURS expanded into ``NAME(1)``..``NAME(n)``)
and, after a write, re-reads every field that overlaps the written one.

Only USAGE DISPLAY bytes are modelled (text and zoned decimal, with GnuCOBOL's
trailing overpunch for signed fields). COMP/COMP-3 fields still get their length,
so offsets stay right, but are not re-read from bytes; nested OCCURS are not
expanded. Numeric fields holding non-digit bytes read back as raw text, so
``IS NUMERIC`` sees what the compiled program sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Any, Iterable, Optional

from src.analysis.dictionary import DataItem, Dictionary

_OVERPUNCH_NEG = "pqrstuvwxy"           # GnuCOBOL (ASCII): negative 0..9 in the last byte


def _usage_length(item: DataItem) -> Optional[int]:
    info = item.picinfo
    digits = (info.int_digits + info.scale) if info else 9
    u = item.usage
    if u in ("COMP-3", "COMPUTATIONAL-3", "PACKED-DECIMAL"):
        return digits // 2 + 1
    if u in ("COMP", "COMP-4", "COMP-5", "BINARY", "COMPUTATIONAL", "COMPUTATIONAL-4", "COMPUTATIONAL-5"):
        return 2 if digits <= 4 else 4 if digits <= 9 else 8
    if u in ("COMP-1", "COMPUTATIONAL-1"):
        return 4
    if u in ("COMP-2", "COMPUTATIONAL-2", "POINTER"):
        return 8
    if u == "INDEX":
        return 4
    return info.length if info else None


@dataclass
class Cell:
    key: str                     # NAME, NAME(3) for an OCCURS element, FILLER#root:offset:n
    item: DataItem
    root: str
    off: int
    length: int
    elementary: bool
    display: bool                # bytes are text / zoned decimal (encodable)
    alias: bool                  # inside a REDEFINES alternative
    entry: dict[str, Any] = field(default_factory=dict)
    view: Optional[str] = None   # the REDEFINES item (alternative layout) this cell belongs to
    area: Optional[str] = None   # the item that alternative redefines

    @property
    def numeric(self) -> bool:
        return self.entry.get("category") in ("numeric", "numeric-edited")


class Storage:
    def __init__(self, dictionary: Dictionary, roots: Optional[Iterable[str]] = None):
        self.d = dictionary
        self.cells: dict[str, Cell] = {}
        self.by_root: dict[str, list[Cell]] = {}
        self.sizes: dict[str, int] = {}
        wanted = {r.upper() for r in roots} if roots is not None else None
        tops = [i for i in dictionary.items if i.parent is None and i.level in (1, 77)]
        for top in tops:
            if top.redefines:
                target = dictionary.lookup(top.redefines)
                root = self._root_of(target) if target is not None else top.name
            else:
                root = top.name
            if wanted is not None and root not in wanted and top.name not in wanted:
                continue
            self._place(top, root, 0, alias=bool(top.redefines), suffix="",
                        view=(top.name, top.redefines) if top.redefines else None)
        for cells in self.by_root.values():
            cells.sort(key=lambda c: (c.off, -c.length))
        self.overlaps: dict[str, list[str]] = {}
        for root, cells in self.by_root.items():
            for c in cells:
                if not c.display:
                    continue
                others = [o.key for o in cells if o is not c and o.display
                          and o.off < c.off + c.length and c.off < o.off + o.length]
                if others:
                    self.overlaps[c.key] = others

    def _root_of(self, item: DataItem) -> str:
        for root, cells in self.by_root.items():
            if any(c.item is item for c in cells):
                return root
        return item.name

    # ---- layout ----------------------------------------------------------------------
    def length(self, item: DataItem) -> int:
        kids = [c for c in item.children if c.level not in (66, 88) and not c.redefines]
        if kids:
            one = sum(self.length(c) for c in kids)
        else:
            one = _usage_length(item) or 0
        return one * (item.occurs or 1)

    def _display(self, item: DataItem) -> bool:
        kids = [c for c in item.children if c.level not in (66, 88)]
        if kids:
            return all(self._display(c) for c in kids)
        return item.usage == "DISPLAY" and item.picinfo is not None

    def _place(self, item: DataItem, root: str, off: int, alias: bool, suffix: str,
               view: Optional[tuple[str, str]] = None) -> int:
        """Add cells for ``item`` (and its children) at ``off``; returns the length placed."""
        if item.redefines and item.parent is not None:
            target = next((c for c in self.by_root.get(root, []) if c.item.name == item.redefines
                           and c.key.endswith(suffix)), None)
            off = target.off if target is not None else off
            alias = True
            if view is None:                     # outermost alternative: one choice per redefined area
                view = (f"{item.name}#{root}:{off}:{len(self.by_root.get(root, []))}", item.redefines)
        n = item.occurs or 1
        one = self.length(item) // n if n else 0
        if item.occurs and suffix:
            n = 1                                   # nested OCCURS: keep the first level only
        for k in range(n):
            sfx = f"({k + 1})" if item.occurs and not suffix else suffix
            start = off + k * one
            if item.name == "FILLER":           # unique: REDEFINES views can hold FILLERs at one offset
                key = f"FILLER#{root}:{start}:{len(self.by_root.get(root, []))}"
            else:
                key = item.name + sfx
            kids = [c for c in item.children if c.level not in (66, 88)]
            cell = Cell(key, item, root, start, one, not kids, self._display(item), alias,
                        item.entry() if item.level != 88 else {},
                        view[0] if view else None, view[1] if view else None)
            self.cells.setdefault(key, cell)
            self.by_root.setdefault(root, []).append(cell)
            pos = start
            for c in kids:
                placed = self._place(c, root, pos, alias, sfx, view)
                if not c.redefines:
                    pos += placed
        self.sizes[root] = max(self.sizes.get(root, 0), off + one * (item.occurs or 1))
        return one * (item.occurs or 1)

    # ---- queries ---------------------------------------------------------------------
    def has_aliases(self, root: str) -> bool:
        return any(c.alias or "(" in c.key for c in self.by_root.get(root, []))

    def fields(self, root: str, include_alias: bool = True) -> list[Cell]:
        """Elementary cells of a record in byte order (base layout first)."""
        cells = [c for c in self.by_root.get(root, []) if c.elementary]
        return [c for c in cells if include_alias or not c.alias]

    # ---- bytes -----------------------------------------------------------------------
    def encode(self, cell: Cell, value: Any) -> str:
        n = cell.length
        if not cell.display:
            return "\0" * n
        if not cell.elementary:
            return str(value if value is not None else "").ljust(n)[:n]
        if cell.numeric and cell.entry.get("category") == "numeric":
            if isinstance(value, str) and value != "" and not _is_number(value):
                return value.ljust(n)[:n]           # raw non-numeric bytes stay as they are
            num = _dec(value)
            scale = int(cell.entry.get("scale") or 0)
            digits = int(abs(num.scaleb(scale)).to_integral_value(rounding=ROUND_DOWN))
            text = str(digits).rjust(n, "0")[-n:]
            if cell.entry.get("signed") and num < 0:
                text = text[:-1] + _OVERPUNCH_NEG[int(text[-1])]
            return text
        text = "" if value is None else (format(value, "f") if isinstance(value, Decimal) else str(value))
        return text.ljust(n)[:n]

    def decode(self, cell: Cell, raw: str) -> Any:
        if not cell.elementary or not cell.numeric or cell.entry.get("category") != "numeric":
            return raw.rstrip()
        neg = False
        body = raw
        if cell.entry.get("signed") and raw and raw[-1] in _OVERPUNCH_NEG:
            neg, body = True, raw[:-1] + str(_OVERPUNCH_NEG.index(raw[-1]))
        if not body.isdigit():
            return raw                              # IS NUMERIC is false; arithmetic sees 0
        scale = int(cell.entry.get("scale") or 0)
        n = Decimal(int(body)).scaleb(-scale)
        return -n if neg else n

    def image(self, root: str, state: dict[str, Any]) -> str:
        """Bytes of a record built from its base (non-REDEFINES) elementary fields."""
        buf = [" "] * self.sizes.get(root, 0)
        for c in self.fields(root, include_alias=False):
            if c.key in state:
                buf[c.off:c.off + c.length] = self.encode(c, state[c.key])
        return "".join(buf)

    def fill(self, root: str, raw: str, state: dict[str, Any], only: Optional[set[str]] = None) -> None:
        """Read every display cell of the record back from ``raw``."""
        for c in self.by_root.get(root, []):
            if c.display and (only is None or c.key in only):
                state[c.key] = self.decode(c, raw[c.off:c.off + c.length])

    def write(self, root: str, raw: str, cell: Cell, value: Any) -> str:
        return raw[:cell.off] + self.encode(cell, value) + raw[cell.off + cell.length:]

    def sync(self, state: dict[str, Any], keys: Iterable[str]) -> None:
        """After writes to ``keys``, update every overlapping field from the shared bytes."""
        for key in keys:
            others = self.overlaps.get(key)
            if not others or key not in state:
                continue
            cell = self.cells[key]
            raw = self._current(cell.root, state)
            raw = self.write(cell.root, raw, cell, state[key])
            self.fill(cell.root, raw, state, only=set(others))
            self._raw[cell.root] = raw

    def _current(self, root: str, state: dict[str, Any]) -> str:
        raw = getattr(self, "_raw", {}).get(root)
        if raw is None:
            raw = self.image(root, state)
        return raw

    def reset(self, state: dict[str, Any], roots: Optional[Iterable[str]] = None) -> None:
        """Start a run: bytes of each record from its base fields, then every view read back."""
        self._raw: dict[str, str] = {}
        for root in (roots if roots is not None else self.by_root):
            if self.has_aliases(root):
                raw = self.image(root, state)
                self._raw[root] = raw
                self.fill(root, raw, state, only={c.key for c in self.by_root[root] if c.alias or not c.elementary})


def _is_number(v: str) -> bool:
    try:
        Decimal(v.strip())
        return v.strip() != ""
    except ArithmeticError:
        return False


def _dec(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return Decimal(str(v))
    try:
        return Decimal(str(v).strip() or "0")
    except ArithmeticError:
        return Decimal(0)
