"""Match predicted rules to gold rules (docs/evaluation.md section 2).

A prediction matches a gold rule when conditions are equivalent, actions are
equivalent, and the traces are in the same program and overlap by at least 80%.
Partial matches are tallied by failure category for error analysis.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from src.common.models import Rule
from src.rules.canon import canon_actions, canon_cond

TRACE_OVERLAP = 0.8


def overlap(a: Rule, b: Rule) -> float:
    if a.trace.program != b.trace.program:
        return 0.0
    lo = max(a.trace.line_start, b.trace.line_start)
    hi = min(a.trace.line_end, b.trace.line_end)
    inter = max(0, hi - lo + 1)
    union = max(a.trace.line_end, b.trace.line_end) - min(a.trace.line_start, b.trace.line_start) + 1
    return inter / union if union else 0.0


@dataclass
class MatchResult:
    n_pred: int = 0
    n_gold: int = 0
    tp: int = 0
    logic_tp: int = 0                       # condition + actions match, trace ignored
    trace_ok: int = 0                       # of logic matches, trace overlap >= 0.8
    unmatched_pred: int = 0                 # predictions with no logically equal gold rule
    failures: Counter = field(default_factory=Counter)   # gold-side failure categories
    examples: list[dict[str, Any]] = field(default_factory=list)

    def add(self, other: "MatchResult") -> None:
        self.n_pred += other.n_pred
        self.n_gold += other.n_gold
        self.tp += other.tp
        self.logic_tp += other.logic_tp
        self.trace_ok += other.trace_ok
        self.unmatched_pred += other.unmatched_pred
        self.failures.update(other.failures)
        self.examples.extend(other.examples)

    @property
    def precision(self) -> float:
        return self.tp / self.n_pred if self.n_pred else 0.0

    @property
    def recall(self) -> float:
        return self.tp / self.n_gold if self.n_gold else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def traceability(self) -> float:
        return self.trace_ok / self.logic_tp if self.logic_tp else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "n_pred": self.n_pred, "n_gold": self.n_gold, "tp": self.tp,
            "precision": round(self.precision, 4), "recall": round(self.recall, 4), "f1": round(self.f1, 4),
            "traceability_accuracy": round(self.traceability, 4),
            "unmatched_predictions": self.unmatched_pred,
            "failures": dict(self.failures),
        }


def match_rules(pred: list[Rule], gold: list[Rule], keep_examples: int = 5) -> MatchResult:
    res = MatchResult(n_pred=len(pred), n_gold=len(gold))
    pk = [(canon_cond(p.conditions), canon_actions(p.actions), canon_actions(p.else_actions)) for p in pred]
    gk = [(canon_cond(g.conditions), canon_actions(g.actions), canon_actions(g.else_actions)) for g in gold]
    used: set[int] = set()
    for gi, g in enumerate(gold):
        best, best_ov = None, -1.0
        for pi, p in enumerate(pred):
            if pi in used or pk[pi] != gk[gi]:
                continue
            ov = overlap(p, g)
            if ov > best_ov:
                best, best_ov = pi, ov
        if best is not None:
            used.add(best)
            res.logic_tp += 1
            if best_ov >= TRACE_OVERLAP:
                res.trace_ok += 1
                res.tp += 1
            else:
                res.failures["trace mismatch"] += 1
            continue
        # no logic match: classify against the best-overlapping unused prediction
        cands = [(overlap(p, g), pi) for pi, p in enumerate(pred) if pi not in used]
        if not cands or max(cands)[0] == 0:
            res.failures["missing rule"] += 1
            reason, other = "missing rule", None
        else:
            _, pi = max(cands)
            other = pred[pi]
            if pk[pi][0] != gk[gi][0] and pk[pi][1:] == gk[gi][1:]:
                reason = "condition mismatch"
            elif pk[pi][0] == gk[gi][0]:
                reason = "action mismatch"
            else:
                reason = "condition and action mismatch"
            res.failures[reason] += 1
        if len(res.examples) < keep_examples:
            res.examples.append({"gold": g.rule_id, "reason": reason, "pred": other.rule_id if other else None,
                                 "gold_key": repr(gk[gi]), "pred_key": repr(pk[pred.index(other)]) if other else None})
    res.unmatched_pred = len(pred) - len(used)
    return res
