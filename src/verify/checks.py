"""Verification checks feeding the confidence score (rules.md section 5, implementation.md 6.2-6.4)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

from src.analysis.analyze import Analysis
from src.common.cobol import Toolchain, parse_display
from src.common.models import Rule, Slice
from src.rules.canon import canon_actions, canon_cond
from src.verify.engine import base, eval_cond, run_rules, store
from src.verify.harness import Harness, build_grid, build_harness, decode_display, encode_record, same_output


def rule_dict(rule: Rule) -> dict[str, Any]:
    return rule.model_dump(mode="python", by_alias=True)


# ---- symbolic check ------------------------------------------------------------------

def field_harness(fields: dict[str, dict[str, Any]]) -> Harness:
    """A pseudo record of the slice's own fields, for programs without a runnable harness."""
    layout = [{"name": n, "pic": e.get("pic"), "length": e.get("length") or 1, "entry": e}
              for n, e in fields.items() if e.get("pic")]
    return Harness("(fields)", layout, [], fields, {})


def structural(rule: Rule, slice_: Slice, rows: list[dict[str, Any]]) -> float:
    """1.0 when the rule is logically the parser's rule; partial credit from grid agreement."""
    same_actions = (canon_actions(rule.actions) == canon_actions(slice_.actions)
                    and canon_actions(rule.else_actions) == canon_actions(slice_.else_actions))
    if canon_cond(rule.conditions) == canon_cond(slice_.condition) and same_actions:
        return 1.0
    rd = rule_dict(rule)["conditions"]
    agree = sum(eval_cond(rd, r) == eval_cond(slice_.condition, r) for r in rows) / max(len(rows), 1)
    return round(agree * (1.0 if same_actions else 0.5), 4)


# ---- differential test --------------------------------------------------------------------

@dataclass
class DiffResult:
    rows: int = 0
    agree: int = 0
    per_rule: dict[int, list[bool]] = field(default_factory=dict)
    mismatches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def agreement(self) -> float:
        return self.agree / self.rows if self.rows else 0.0

    def rule_agreement(self, k: int) -> Optional[float]:
        hits = self.per_rule.get(k)
        return sum(hits) / len(hits) if hits else None


def differential(analysis: Analysis, rules: list[Rule], exe: Path, toolchain: Toolchain,
                 harness: Optional[Harness] = None, seed: int = 7) -> tuple[DiffResult, list[dict[str, Any]]]:
    """Run the compiled program and the extracted rules on the same grid; compare all outputs."""
    h = harness or build_harness(analysis)
    if h is None:
        raise ValueError("program does not follow the ACCEPT/DISPLAY harness convention")
    rds = [rule_dict(r) for r in rules]
    conds = [r["conditions"] for r in rds] + [s.condition for s in analysis.slices]
    rows = build_grid(h, conds, seed=seed)
    outputs = toolchain.run_many(exe, [encode_record(h, r) for r in rows])
    res = DiffResult()
    slice_by_rule = {k: s for k, s in enumerate(analysis.slices)} if len(analysis.slices) == len(rules) else {}
    for row, raw in zip(rows, outputs):
        state = dict(h.initial)
        for f in h.layout:
            state[f["name"]] = store(row.get(f["name"], ""), f["entry"])
        inputs = dict(state)
        fired = set(run_rules(rds, state, h.fields))
        actual = parse_display(raw)
        ok = all(o in actual and same_output(state.get(base(o)), decode_display(actual[o], h.fields.get(base(o))),
                                             h.fields.get(base(o))) for o in h.outputs)
        res.rows += 1
        res.agree += ok
        for k in range(len(rds)):
            s = slice_by_rule.get(k)
            if k in fired or (s is not None and eval_cond(s.condition, inputs)):
                res.per_rule.setdefault(k, []).append(ok)
        if not ok and len(res.mismatches) < 5:
            res.mismatches.append({"inputs": {k: str(v) for k, v in inputs.items() if k in row},
                                   "expected": {o: str(state.get(base(o))) for o in h.outputs},
                                   "cobol": {o: actual.get(o) for o in h.outputs}})
    return res, rows


# ---- self-consistency --------------------------------------------------------------------

_STOP = {"the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "with", "is", "are", "when", "than",
         "at", "least", "more", "less", "that", "by", "be", "as", "any", "all", "other", "their", "its"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9.%]+", text.lower()) if w not in _STOP}


def consistency_score(samples: list[Any]) -> float:
    """Agreement of field names and intent wording across several sampled enrichments."""
    valid = [s for s in samples if s is not None]
    if len(valid) < 2:
        return 0.5 if valid else 0.0
    keys = {k.upper() for s in valid for k in s.field_names}
    name_scores = []
    for k in keys:
        votes = [str({kk.upper(): vv for kk, vv in s.field_names.items()}.get(k, "")).lower() for s in valid]
        top = max(votes.count(v) for v in set(votes))
        name_scores.append(top / len(votes))
    names = sum(name_scores) / len(name_scores) if name_scores else 1.0
    jacc = []
    for a, b in combinations(valid, 2):
        wa, wb = _words(a.intent), _words(b.intent)
        jacc.append(len(wa & wb) / len(wa | wb) if wa | wb else 1.0)
    return round(0.5 * names + 0.5 * (sum(jacc) / len(jacc)), 4)


# ---- naming clarity -------------------------------------------------------------------------

def _abbrev_match(token: str, words: set[str]) -> bool:
    """'cust' ~ 'customer', 'amt' ~ 'amount' (prefix or ordered-letter abbreviation)."""
    for w in words:
        if token == w or (len(token) >= 3 and (w.startswith(token) or token.startswith(w) and len(w) >= 3)):
            return True
        if len(token) >= 2 and token[0] == w[0] and _subsequence(token, w):
            return True
    return False


def _subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def naming_score(rule: Rule, slice_: Slice) -> float:
    pairs: list[tuple[str, str]] = []

    def walk(c: dict[str, Any]) -> None:
        for k in ("all", "any"):
            for x in c.get(k) or []:
                walk(x)
        if c.get("not"):
            walk(c["not"])
        if c.get("source_name"):
            pairs.append((base(c["source_name"]), c["field"]))

    walk(rule_dict(rule)["conditions"])
    for a in rule.actions + rule.else_actions:
        if a.type in ("set", "compute") and a.source_name and a.target:
            pairs.append((base(a.source_name), a.target))
    if not pairs:
        return 1.0
    scores = []
    for cobol, business in dict(pairs).items():
        entry = slice_.fields.get(cobol)
        if entry is None:
            scores.append(0.0)
            continue
        btoks = [t for t in business.split("_") if t]
        if entry.get("comment"):
            cwords = set(re.findall(r"[a-z0-9]+", entry["comment"].lower()))
            hit = any(_abbrev_match(t, cwords) for t in btoks)
            scores.append(1.0 if hit else 0.4)
        else:
            nwords = set(re.findall(r"[a-z0-9]+", cobol.lower()))
            hit = any(_abbrev_match(t, nwords) for t in btoks)
            readable = all(len(t) >= 3 for t in btoks)
            scores.append(0.7 if hit and readable else 0.5)
    return round(sum(scores) / len(scores), 4)
