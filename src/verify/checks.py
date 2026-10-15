"""Verification checks feeding the confidence score (rules.md section 5, implementation.md 6.2-6.4)."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

from src.analysis.analyze import Analysis
from src.common.cobol import Toolchain, parse_display
from src.common.models import Rule, Slice
from src.rules.canon import canon_actions, canon_cond
from src.verify.engine import base, eval_cond, is_numeric_entry, run_rules, store, to_num
from src.verify import smt
from src.verify.harness import (Harness, build_grid, build_harness, candidates, decode_display, encode_record,
                                normalize, same_output, same_raw)
from src.verify.interpreter import RuleInterpreter, SimulationLimit


def rule_dict(rule: Rule) -> dict[str, Any]:
    return rule.model_dump(mode="python", by_alias=True)


# ---- symbolic check ------------------------------------------------------------------

def field_harness(fields: dict[str, dict[str, Any]]) -> Harness:
    """A pseudo record of the slice's own fields, for programs without a runnable harness."""
    layout = [{"name": n, "pic": e.get("pic"), "length": e.get("length") or 1, "entry": e}
              for n, e in fields.items() if e.get("pic")]
    return Harness("(fields)", layout, [], fields, {})


def numeric_fields(fields: dict[str, dict[str, Any]]) -> set[str]:
    return {n for n, e in fields.items() if is_numeric_entry(e)}


def structural(rule: Rule, slice_: Slice, rows: list[dict[str, Any]]) -> float:
    """1.0 when the rule is logically the parser's rule; partial credit from grid agreement.

    Same canonical form, or a different form the SMT solver proves equivalent, is an
    exact match. Otherwise the score is the share of grid rows on which both agree,
    capped below 1.0 (the solver found, or could not rule out, an input where they differ).
    """
    same_actions = (canon_actions(rule.actions) == canon_actions(slice_.actions)
                    and canon_actions(rule.else_actions) == canon_actions(slice_.else_actions))
    rd = rule_dict(rule)["conditions"]
    if canon_cond(rule.conditions) == canon_cond(slice_.condition):
        if same_actions:
            return 1.0
    elif smt.available():
        try:
            proven, _ = smt.equivalent(rd, slice_.condition, numeric_fields(slice_.fields))
        except smt.Unsupported:
            proven = None
        if proven and same_actions:
            return 1.0
    agree = sum(eval_cond(rd, r) == eval_cond(slice_.condition, r) for r in rows) / max(len(rows), 1)
    return round(min(agree, 0.99) * (1.0 if same_actions else 0.5), 4)


def satisfiable(rule: Rule, slice_: Optional[Slice] = None) -> Optional[bool]:
    """False when no input can make the rule fire (its condition, with its callers' context,
    is a contradiction); None when the solver is unavailable or cannot decide."""
    if not smt.available():
        return None
    d = rule_dict(rule)
    conds = [d["conditions"]] + ([d["context"]] if d.get("context") else [])
    try:
        ok, _ = smt.witness(conds, numeric_fields(slice_.fields) if slice_ else None)
    except smt.Unsupported:
        return None
    return ok


# ---- differential test --------------------------------------------------------------------

@dataclass
class DiffResult:
    rows: int = 0
    agree: int = 0
    per_rule: dict[int, list[bool]] = field(default_factory=dict)
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    unsimulated: int = 0                   # rows the rule model could not run (loop limits, GO TO DEPENDING)
    notes: set[str] = field(default_factory=set)

    @property
    def agreement(self) -> float:
        return self.agree / self.rows if self.rows else 0.0

    def rule_agreement(self, k: int) -> Optional[float]:
        hits = self.per_rule.get(k)
        return sum(hits) / len(hits) if hits else None


def explore(h: Harness, interp: RuleInterpreter, rows: list[dict[str, Any]], conds: list[dict[str, Any]],
            seed: int = 7, budget: int = 3000, max_new: int = 200, per_pair: int = 3) -> list[dict[str, Any]]:
    """Coverage-guided search for rows where rules fire *together*.

    The grid targets each condition on the inputs, but some rules only fire after earlier
    rules changed the state (a sign flag set during validation, then an ADD). Starting from
    the grid, mutate one to three input fields that conditions read and keep every row that
    makes a pair of rules fire in the same run that fewer than ``per_pair`` rows reached so far
    (judged with the rule model only), so each combination is seen with varied data."""
    from src.verify.engine import base as _base
    from src.verify.smt import _leaves
    rng = random.Random(seed + 1)
    cands = candidates(h, conds, rng)
    read = {_base(l[k]) for c in conds for l in _leaves(c) for k in ("source_name", "ref") if l.get(k)}
    read |= h.loop_bounds
    names = [f["name"] for f in h.layout if _base(f["name"]) in read]
    if h.storage is not None:             # and every field sharing their bytes
        extra = {o for n in names for o in h.storage.overlaps.get(n, [])}
        names += [f["name"] for f in h.layout if f["name"] in extra and f["name"] not in names]
    names = names or [f["name"] for f in h.layout]

    def fired(row: dict[str, Any]) -> Optional[frozenset]:
        """Rules that fired, split by whether their actions changed a value (a rule that
        multiplies zero by -1 fires without effect, and hides a missing sign flip)."""
        state = {**h.initial, **row}
        try:
            f = interp.run(state)
        except SimulationLimit:
            return None
        return frozenset((k, k in interp.effective) for k in f)

    covered: dict[tuple[int, int], int] = {}
    counts: dict[int, int] = {}
    pool: list[tuple[dict[str, Any], frozenset]] = []
    for r in rows:
        f = fired(r)
        if f:
            for p in {(a, b) for a in f for b in f if a <= b}:
                covered[p] = covered.get(p, 0) + 1
            pool.append((r, f))
            for k in f:
                counts[k] = counts.get(k, 0) + 1
    if not pool:
        return []

    def parent() -> dict[str, Any]:              # favour rows that reach rarely fired rules
        best = max(rng.sample(pool, min(5, len(pool))),
                   key=lambda p: sum(1 / counts.get(k, 1) for k in p[1]))
        return best[0]

    new: list[dict[str, Any]] = []
    groups: dict[str, list[str]] = {}            # CUST-MONTH(1..12) count as one field to pick
    for n in names:
        groups.setdefault(_base(n), []).append(n)
    for _ in range(budget):
        child = dict(parent())
        picks = rng.sample(list(groups), min(len(groups), rng.randint(1, 3)))
        changed = tuple(rng.choice(groups[g]) for g in picks)
        for n in changed:
            child[n] = rng.choice(cands[n])
        child = normalize(h, child, rng, force=changed)
        f = fired(child)
        if not f:
            continue
        pairs = {(a, b) for a in f for b in f if a <= b}
        if any(covered.get(p, 0) < per_pair for p in pairs):
            for p in pairs:
                covered[p] = covered.get(p, 0) + 1
            pool.append((child, f))
            for k in f:
                counts[k] = counts.get(k, 0) + 1
            new.append(child)
            if len(new) >= max_new:
                break
    return new


def differential(analysis: Analysis, rules: list[Rule], exe: Path, toolchain: Toolchain,
                 harness: Optional[Harness] = None, seed: int = 7,
                 explore_budget: Optional[int] = None) -> tuple[DiffResult, list[dict[str, Any]]]:
    """Run the compiled program and the extracted rules on the same grid; compare all outputs.

    ``explore_budget`` adds coverage-guided rows (``explore``); by default only for record-level
    (subprogram) harnesses, so results for ACCEPT/DISPLAY programs stay as published."""
    h = harness or build_harness(analysis)
    if h is None:
        raise ValueError("program does not follow the ACCEPT/DISPLAY harness convention")
    rds = [rule_dict(r) for r in rules]
    conds = [r["conditions"] for r in rds] + [s.condition for s in analysis.slices]
    rows = build_grid(h, conds, seed=seed)
    res = DiffResult()
    slice_by_rule = {k: s for k, s in enumerate(analysis.slices)} if len(analysis.slices) == len(rules) else {}
    interp = (RuleInterpreter(analysis, rds, h.fields, h.storage, h.records or None)
              if analysis.slice_stmts and slice_by_rule else None)
    budget = explore_budget if explore_budget is not None else (3000 if h.storage is not None else 0)
    if interp is not None and budget:
        rows = rows + explore(h, interp, rows, conds, seed=seed, budget=budget)
    outputs = toolchain.run_many(exe, [encode_record(h, r) for r in rows])
    for row, raw in zip(rows, outputs):
        state = dict(h.initial)
        if h.storage is not None:
            state.update(row)                  # already one consistent record image per parameter
        else:
            for f in h.layout:
                state[f["name"]] = store(row.get(f["name"], ""), f["entry"])
        inputs = dict(state)
        if interp is not None:
            try:
                fired = interp.run(state)
            except SimulationLimit as exc:
                res.unsimulated += 1
                res.notes.add(str(exc))
                continue
        else:
            fired = set(run_rules(rds, state, h.fields))
        actual = parse_display(raw)

        def expected(o: str) -> Any:
            if h.storage is not None and o in h.storage.by_root and not h.storage.cells[o].elementary:
                return h.storage.image(o, state)       # the record's bytes after the run
            return state[o] if o in state else state.get(base(o))

        def same(o: str) -> bool:
            entry = h.fields.get(base(o))
            exp = expected(o)
            if o not in actual:
                return False
            if h.storage is not None and o in h.storage.by_root and not h.storage.cells[o].elementary:
                return exp.rstrip() == actual[o].rstrip()     # record bytes
            if is_numeric_entry(entry) and isinstance(exp, str) and to_num(exp) is None:
                return same_raw(exp, actual[o], entry)      # non-digit bytes left by a REDEFINES view
            return same_output(exp, decode_display(actual[o], entry), entry)
        ok = all(same(o) for o in h.outputs)
        res.rows += 1
        res.agree += ok
        for k in range(len(rds)):
            s = slice_by_rule.get(k)
            if k in fired or (s is not None and eval_cond(s.condition, inputs)):
                res.per_rule.setdefault(k, []).append(ok)
        if not ok and len(res.mismatches) < 5:
            res.mismatches.append({"inputs": {k: str(v) for k, v in inputs.items() if k in row},
                                   "expected": {o: str(expected(o)) for o in h.outputs},
                                   "cobol": {o: actual.get(o) for o in h.outputs},
                                   **({"fields": _field_diff(h, state, actual)} if h.storage is not None else {})})
    return res, rows


def _field_diff(h: Harness, state: dict[str, Any], actual: dict[str, str]) -> dict[str, list[str]]:
    """Fields whose bytes differ between the model's record and the program's record."""
    out: dict[str, list[str]] = {}
    for root in h.outputs:
        if root not in h.storage.by_root or h.storage.cells[root].elementary or root not in actual:
            continue
        mine, theirs = h.storage.image(root, state), actual[root].ljust(h.storage.sizes[root])
        for c in h.storage.fields(root, include_alias=False):
            a, b = mine[c.off:c.off + c.length], theirs[c.off:c.off + c.length]
            if a.rstrip() != b.rstrip() and not c.key.startswith("FILLER#"):
                out[c.key] = [a, b]
    return out


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
