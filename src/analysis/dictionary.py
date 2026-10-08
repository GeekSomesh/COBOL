"""Data dictionary: one typed entry per DATA DIVISION item (implementation.md 2.2 step 3)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

NUMERIC_USAGES = {"COMP", "COMP-1", "COMP-2", "COMP-3", "COMP-4", "COMP-5", "COMPUTATIONAL",
                  "COMPUTATIONAL-1", "COMPUTATIONAL-2", "COMPUTATIONAL-3", "COMPUTATIONAL-4",
                  "COMPUTATIONAL-5", "BINARY", "PACKED-DECIMAL", "INDEX"}


@dataclass
class PicInfo:
    category: str           # numeric | alphanumeric | alphabetic | numeric-edited | alphanumeric-edited
    int_digits: int = 0
    scale: int = 0          # digits after the implied decimal point
    signed: bool = False
    length: int = 0         # characters in DISPLAY usage

    @property
    def is_numeric(self) -> bool:
        return self.category == "numeric"


def parse_pic(pic: str) -> PicInfo:
    """Interpret a PICTURE string, e.g. ``S9(7)V99`` -> 7 integer digits, scale 2, signed."""
    expanded = re.sub(r"(.)\((\d+)\)", lambda m: m.group(1) * int(m.group(2)), pic.upper())
    signed = expanded.startswith("S")
    body = expanded[1:] if signed else expanded
    if re.fullmatch(r"[9VP]+", body or "X"):
        int_part, _, frac_part = body.partition("V")
        return PicInfo("numeric", int_part.count("9"), frac_part.count("9"), signed,
                       body.count("9"))
    if re.fullmatch(r"A+", body):
        return PicInfo("alphabetic", length=len(body))
    if re.fullmatch(r"X+", body):
        return PicInfo("alphanumeric", length=len(body))
    if re.fullmatch(r"[AX9]+", body):
        return PicInfo("alphanumeric", length=len(body))
    if re.search(r"[Z*+\-$,.CRDB0/]", body) and "X" not in body:
        int_part, _, frac_part = body.replace(".", "V").partition("V")
        digits = sum(int_part.count(c) for c in "9Z*")
        return PicInfo("numeric-edited", digits, sum(frac_part.count(c) for c in "9Z*"),
                       "+" in body or "-" in body or "CR" in body or "DB" in body,
                       len(body.replace("V", "")))
    return PicInfo("alphanumeric-edited", length=len(body.replace("V", "")))


@dataclass
class DataItem:
    level: int
    name: str                                   # "FILLER" for unnamed items
    file: str
    line: int
    section: str = "WORKING-STORAGE"
    pic: Optional[str] = None
    usage: str = "DISPLAY"
    value: Any = None
    redefines: Optional[str] = None
    occurs: Optional[int] = None
    values88: list[list[Any]] = field(default_factory=list)   # for level 88: [[v], [lo, hi], ...]
    parent: Optional[DataItem] = field(default=None, repr=False)
    children: list[DataItem] = field(default_factory=list, repr=False)
    comment: Optional[str] = None

    @property
    def is_group(self) -> bool:
        return self.pic is None and self.level not in (88, 66) and bool(
            [c for c in self.children if c.level != 88])

    @property
    def is_condition(self) -> bool:
        return self.level == 88

    @property
    def picinfo(self) -> Optional[PicInfo]:
        if self.pic:
            info = parse_pic(self.pic)
            if self.usage in NUMERIC_USAGES and info.category != "numeric":
                info.category = "numeric"
            return info
        if self.usage in NUMERIC_USAGES:
            return PicInfo("numeric", 9, 0, True, 4)
        return None

    @property
    def category(self) -> str:
        if self.is_condition:
            return "condition"
        if self.is_group:
            return "group"
        info = self.picinfo
        return info.category if info else "alphanumeric"

    def conditions88(self) -> dict[str, list[list[Any]]]:
        return {c.name: c.values88 for c in self.children if c.level == 88}

    def entry(self) -> dict[str, Any]:
        """Serializable summary used in Slice.fields and LLM prompts."""
        info = self.picinfo
        out: dict[str, Any] = {
            "level": self.level,
            "pic": self.pic,
            "usage": self.usage,
            "category": self.category,
            "values88": self.conditions88(),
        }
        if info and info.category in ("numeric", "numeric-edited"):
            out.update(int_digits=info.int_digits, scale=info.scale, signed=info.signed)
        if info:
            out["length"] = info.length
        if self.value is not None:
            out["value"] = self.value
        if self.redefines:
            out["redefines"] = self.redefines
        if self.occurs:
            out["occurs"] = self.occurs
        if self.parent is not None and self.parent.name != "FILLER":
            out["parent"] = self.parent.name
        if self.comment:
            out["comment"] = self.comment
        out["defined_in"] = {"file": self.file, "line": self.line}
        return out


class Dictionary:
    def __init__(self) -> None:
        self.items: list[DataItem] = []
        self._by_name: dict[str, list[DataItem]] = {}

    def add(self, item: DataItem) -> None:
        self.items.append(item)
        if item.name != "FILLER":
            self._by_name.setdefault(item.name, []).append(item)

    def lookup(self, name: str, qualifier: Optional[str] = None) -> Optional[DataItem]:
        base = name.split("(")[0].strip().upper()
        found = self._by_name.get(base, [])
        if qualifier and len(found) > 1:
            for item in found:
                p = item.parent
                while p is not None:
                    if p.name == qualifier.upper():
                        return item
                    p = p.parent
        return found[0] if found else None

    def condition_name(self, name: str) -> Optional[DataItem]:
        item = self.lookup(name)
        return item if item is not None and item.is_condition else None

    def __contains__(self, name: str) -> bool:
        return self.lookup(name) is not None

    def entries(self) -> dict[str, dict[str, Any]]:
        return {i.name: i.entry() for i in self.items if i.name != "FILLER" and i.level != 88}
