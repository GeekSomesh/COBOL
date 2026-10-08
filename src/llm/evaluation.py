"""Score enrichment output against gold rules (names, intent, concepts, grounding)."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from src.common.models import Rule, Slice
from src.rules.canon import rule_key

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
_NUM = re.compile(r"(?<![A-Za-z])(\d[\d,]*(?:\.\d+)?)")


def name_map(program: str) -> dict[str, str]:
    """COBOL name -> gold business name, from the generator spec (or the gold rules)."""
    spec_path = DATA / "specs" / f"{program}.yaml"
    if spec_path.exists():
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        return {f["name"]: f["business"] for f in spec["inputs"] + spec["outputs"]}
    names: dict[str, str] = {}
    doc = json.loads((DATA / "gold" / f"{program}.json").read_text(encoding="utf-8"))

    def walk(c: dict[str, Any]) -> None:
        for k in ("all", "any"):
            for x in c.get(k) or []:
                walk(x)
        if c.get("not"):
            walk(c["not"])
        if "source_name" in c:
            names[c["source_name"]] = c["field"]

    for r in doc["rules"]:
        walk(r["conditions"])
        for a in r["actions"] + r["else_actions"]:
            if a.get("target") and a.get("source_name"):
                names[a["source_name"]] = a["target"]
    return names


def gold_enrichment(s: Slice, gold: Rule, names: dict[str, str]) -> dict[str, Any]:
    return {"title": gold.title, "intent": gold.intent,
            "field_names": {n: names[n] for n in s.fields if n in names},
            "concepts": gold.concepts}


def pair(rules: list[Rule], gold: list[Rule]) -> list[tuple[int, int]]:
    """(pred index, gold index) pairs with identical canonical logic, in order."""
    gk = [rule_key(g) for g in gold]
    used: set[int] = set()
    out = []
    for i, r in enumerate(rules):
        k = rule_key(r)
        for j, g in enumerate(gk):
            if j not in used and g == k:
                used.add(j)
                out.append((i, j))
                break
    return out


def numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(text):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def grounded(intent: str, s: Slice) -> bool:
    """Every number in the intent appears in the rule or its code (as is, or as a percentage)."""
    allowed: set[float] = set()

    def add(v: Any) -> None:
        if isinstance(v, list):
            for x in v:
                add(x)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            allowed.update({round(float(v), 6), round(float(v) * 100, 6)})

    def walk(c: dict[str, Any]) -> None:
        for k in ("all", "any"):
            for x in c.get(k) or []:
                walk(x)
        if c.get("not"):
            walk(c["not"])
        if "value" in c:
            add(c["value"])

    walk(s.condition)
    for a in s.actions + s.else_actions:
        add(a.get("value"))
    context = s.source_text + " " + " ".join(f"{n} {e.get('comment', '')}" for n, e in s.fields.items())
    for v in numbers(context):
        add(v)
    return all(round(n, 6) in allowed for n in numbers(intent))


def tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}


def token_f1(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if not inter:
        return 0.0
    p, r = inter / len(ta), inter / len(tb)
    return 2 * p * r / (p + r)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class EnrichScores:
    rules: int = 0
    valid: int = 0
    name_total: int = 0
    name_exact: int = 0
    name_token_f1: float = 0.0
    concept_jaccard: float = 0.0
    grounded: int = 0
    seconds: float = 0.0
    intent_pairs: list[tuple[str, str]] = field(default_factory=list)
    title_pairs: list[tuple[str, str]] = field(default_factory=list)
    intent_sim: Optional[float] = None
    title_sim: Optional[float] = None
    examples: list[dict[str, Any]] = field(default_factory=list)

    def add(self, s: Slice, pred: Rule, enrichment: Any, gold: dict[str, Any]) -> None:
        self.rules += 1
        if enrichment is None:
            self.name_total += len(gold["field_names"])
            return
        self.valid += 1
        names = {**{k.upper(): v for k, v in enrichment.field_names.items()}}
        for cobol, want in gold["field_names"].items():
            got = names.get(cobol.upper(), "")
            got = re.sub(r"[^a-z0-9]+", "_", got.lower()).strip("_")
            self.name_total += 1
            self.name_exact += got == want
            self.name_token_f1 += token_f1(got, want)
        pc, gc = set(pred.concepts), set(gold["concepts"])
        self.concept_jaccard += len(pc & gc) / len(pc | gc) if pc | gc else 1.0
        self.grounded += grounded(pred.intent, s)
        self.intent_pairs.append((pred.intent, gold["intent"]))
        self.title_pairs.append((pred.title, gold["title"]))
        if len(self.examples) < 6:
            self.examples.append({"rule": pred.rule_id, "title": pred.title, "intent": pred.intent,
                                  "gold_intent": gold["intent"], "names": enrichment.field_names,
                                  "gold_names": gold["field_names"], "concepts": pred.concepts})

    def finish(self, embedder) -> None:
        if self.intent_pairs:
            texts = [t for pair_ in self.intent_pairs for t in pair_]
            vecs = embedder(texts)
            self.intent_sim = sum(cosine(vecs[i], vecs[i + 1]) for i in range(0, len(vecs), 2)) / len(self.intent_pairs)
            texts = [t for pair_ in self.title_pairs for t in pair_]
            vecs = embedder(texts)
            self.title_sim = sum(cosine(vecs[i], vecs[i + 1]) for i in range(0, len(vecs), 2)) / len(self.title_pairs)

    def summary(self) -> dict[str, Any]:
        v = max(self.valid, 1)
        return {
            "rules": self.rules,
            "schema_valid_rate": round(self.valid / max(self.rules, 1), 4),
            "field_name_exact": round(self.name_exact / max(self.name_total, 1), 4),
            "field_name_token_f1": round(self.name_token_f1 / max(self.name_total, 1), 4),
            "intent_similarity": None if self.intent_sim is None else round(self.intent_sim, 4),
            "title_similarity": None if self.title_sim is None else round(self.title_sim, 4),
            "concept_jaccard": round(self.concept_jaccard / v, 4),
            "intent_numbers_grounded": round(self.grounded / v, 4),
            "seconds_per_rule": round(self.seconds / max(self.rules, 1), 3),
            "examples": self.examples,
        }
