"""Business-concept search over rules (FR-16, implementation.md 7.3).

Keyword match over concepts, title, intent and field names with a small synonym
map, plus number-aware matching ("customers under 18" finds ``age < 18``).
When a local embedding model is available, semantic similarity is added.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Optional

from src.common.models import Rule

SYNONYMS: dict[str, list[str]] = {
    "minor": ["age", "minor", "young", "under"], "minors": ["age", "minor"],
    "customer": ["customer", "client", "cust"], "customers": ["customer", "client"],
    "age": ["age", "minor", "senior", "older"], "senior": ["senior", "age", "older"],
    "amount": ["amount", "amt", "value", "transaction"], "transaction": ["transaction", "txn", "amount"],
    "fee": ["fee", "charge", "waiver", "surcharge"], "fees": ["fee", "charge"],
    "rate": ["rate", "interest"], "interest": ["interest", "rate"],
    "late": ["late", "days", "penalty", "overdue"], "penalty": ["penalty", "late", "fee"],
    "credit": ["credit", "score", "limit"], "loan": ["loan", "ltv", "mortgage", "term"],
    "geography": ["geography", "country", "region"], "country": ["country", "geography", "region"],
    "fraud": ["fraud", "review", "risk", "hold", "alert"], "risk": ["risk", "fraud", "review"],
    "waive": ["waiver", "waive", "waived"], "waiver": ["waiver", "waived"],
    "student": ["student"], "bankruptcy": ["bankruptcy", "bk"], "approve": ["approval", "approve"],
}
STOP = {"show", "all", "rules", "rule", "the", "a", "an", "of", "for", "about", "with", "that", "which",
        "affecting", "affect", "find", "me", "list", "is", "are", "in", "on", "to", "and", "or", "who"}
NUMERIC = [
    (r"(?:under|below|less than|younger than|<)\s*\$?([\d,.]+)", ("<", "<=")),
    (r"(?:over|above|more than|greater than|older than|exceeds?|>)\s*\$?([\d,.]+)", (">", ">=")),
    (r"(?:at least|minimum of|>=)\s*\$?([\d,.]+)", (">=", ">")),
    (r"(?:at most|maximum of|<=)\s*\$?([\d,.]+)", ("<=", "<")),
]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def query_terms(q: str) -> tuple[set[str], list[tuple[set[str], float]]]:
    terms: set[str] = set()
    for w in _words(q):
        if w in STOP or w.replace(".", "").isdigit():
            continue
        terms.add(_stem(w))
        terms.update(_stem(x) for x in SYNONYMS.get(w, []))
    numbers: list[tuple[set[str], float]] = []
    for pattern, ops in NUMERIC:
        for m in re.finditer(pattern, q.lower()):
            try:
                numbers.append((set(ops), float(m.group(1).replace(",", "").rstrip("."))))
            except ValueError:
                pass
    return terms, numbers


def _leaves(c: Any, out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    d = c if isinstance(c, dict) else c.model_dump(by_alias=True)
    for k in ("all", "any"):
        for x in d.get(k) or []:
            _leaves(x, out)
    if d.get("not"):
        _leaves(d["not"], out)
    if d.get("source_name"):
        out.append(d)
    return out


def rule_text(rule: Rule) -> str:
    leaves = _leaves(rule.conditions, [])
    targets = [a.target or "" for a in rule.actions + rule.else_actions]
    return " ".join([rule.title, rule.intent, " ".join(rule.concepts), " ".join(l["field"] for l in leaves),
                     " ".join(targets), rule.domain.replace("_", " ")])


def keyword_score(rule: Rule, terms: set[str], numbers: list[tuple[set[str], float]]) -> float:
    if not terms and not numbers:
        return 1.0
    score = 0.0
    concepts = {_stem(w) for c in rule.concepts for w in _words(c)}
    title = {_stem(w) for w in _words(rule.title)}
    body = {_stem(w) for w in _words(rule_text(rule))}
    for t in terms:
        if t in concepts:
            score += 3
        elif t in title:
            score += 2
        elif t in body:
            score += 1
    for ops, n in numbers:
        best = 0
        for leaf in _leaves(rule.conditions, []):
            vals = leaf.get("value") if isinstance(leaf.get("value"), list) else [leaf.get("value")]
            if not any(isinstance(v, (int, float)) and abs(float(v) - n) < 1e-9 for v in vals):
                continue
            if leaf["op"] in ops:
                best = max(best, 4)          # "under 18" matches "< 18"
            elif leaf["op"] in ("between", "==", "<", "<=", ">", ">="):
                best = max(best, 1)          # same number, different comparison
        score += best
    return score


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def search(rules: list[tuple[Rule, Any]], q: Optional[str],
           embed_query: Optional[Callable[[str], list[float]]] = None,
           rule_vector: Optional[Callable[[Rule], Optional[list[float]]]] = None) -> list[tuple[Rule, Any, float]]:
    if not q or not q.strip():
        return [(r, p, 0.0) for r, p in rules]
    terms, numbers = query_terms(q)
    qvec = None
    if embed_query is not None:
        try:
            qvec = embed_query(q)
        except Exception:
            qvec = None
    out = []
    for r, p in rules:
        s = keyword_score(r, terms, numbers)
        if qvec is not None and rule_vector is not None:
            vec = rule_vector(r)
            if vec is not None:
                sim = cosine(qvec, vec)
                s += max(0.0, sim - 0.5) * 10       # only clearly related rules gain
        if s > 0:
            out.append((r, p, round(s, 3)))
    out.sort(key=lambda x: (-x[2], x[0].rule_id))
    return out
