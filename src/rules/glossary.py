"""Business glossary: the organisation's own words for COBOL names and abbreviations.

``data/glossary.json`` (or the file named by ``COBOL_GLOSSARY``)::

    {"fields": {"WS-CSCR": "credit_score"},          # exact COBOL name -> business name
     "terms":  {"CSCR": "credit score", "AMT": "amount", "DTI": "debt to income"}}

* ``fields`` are authoritative: they override both the mechanical baseline name and
  the LLM's suggestion, so a reviewed name never drifts between runs.
* ``terms`` expand abbreviations in the baseline name (``CUST-AMT`` -> ``customer_amount``)
  and are shown to the LLM as the organisation's vocabulary.

Without a glossary file nothing changes (the evaluation runs without one). The
API edits it through ``GET/PUT /v1/glossary`` (admin).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[2]
_SNAKE = re.compile(r"[^a-z0-9]+")


def snake(text: str) -> str:
    return _SNAKE.sub("_", text.lower()).strip("_")


@dataclass
class Glossary:
    fields: dict[str, str] = field(default_factory=dict)
    terms: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "Glossary":
        fields = {str(k).upper().strip(): snake(str(v)) for k, v in (data.get("fields") or {}).items() if snake(str(v))}
        terms = {str(k).upper().strip(): str(v).strip().lower() for k, v in (data.get("terms") or {}).items()
                 if str(v).strip()}
        return cls(fields, terms)

    def as_dict(self) -> dict:
        return {"fields": dict(sorted(self.fields.items())), "terms": dict(sorted(self.terms.items()))}

    def field_name(self, source_name: str) -> Optional[str]:
        """The authoritative business name of a COBOL field, if the glossary has one."""
        return self.fields.get(source_name.split("(")[0].strip().upper())

    def expand(self, source_name: str) -> Optional[str]:
        """Baseline name with abbreviations expanded; None when no term applies."""
        exact = self.field_name(source_name)
        if exact:
            return exact
        tokens = [t for t in re.split(r"[^A-Z0-9]+", source_name.upper()) if t]
        if not any(t in self.terms for t in tokens):
            return None
        return snake(" ".join(self.terms.get(t, t.lower()) for t in tokens))

    def prompt_lines(self, names: Iterable[str]) -> list[str]:
        """Glossary entries relevant to these COBOL names, for the LLM prompt."""
        out: list[str] = []
        seen: set[str] = set()
        for name in names:
            exact = self.field_name(name)
            if exact and name not in seen:
                out.append(f"{name} = {exact} (use this name)")
                seen.add(name)
            for t in re.split(r"[^A-Z0-9]+", name.upper()):
                if t in self.terms and t not in seen:
                    out.append(f"{t} = {self.terms[t]}")
                    seen.add(t)
        return out

    def __bool__(self) -> bool:
        return bool(self.fields or self.terms)


def path() -> Path:
    return Path(os.environ.get("COBOL_GLOSSARY") or ROOT / "data" / "glossary.json")


_cache: tuple[Optional[float], str, Glossary] = (None, "", Glossary())


def active() -> Glossary:
    """The glossary on disk (re-read when the file changes); empty when there is none."""
    global _cache
    p = path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return Glossary()
    if _cache[0] != mtime or _cache[1] != str(p):
        _cache = (mtime, str(p), Glossary.from_dict(json.loads(p.read_text(encoding="utf-8"))))
    return _cache[2]


def save(g: Glossary) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(g.as_dict(), indent=2), encoding="utf-8")
