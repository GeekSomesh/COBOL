"""Stateless scoring, SHAP explanations and conflict detection over extracted rules.

* ``score``: apply approved rules of a rule set to business-named inputs
  (FR-23). Inputs are never stored or logged (security.md T8).
* ``explain``: SHAP feature influence from a surrogate model trained on decisions
  replayed through the rules (FR-21). The rules are already explicit; the
  surrogate only summarises which inputs matter most.
* ``conflicts``: duplicate, overlapping and conflicting rule pairs (FR-22).
"""

from __future__ import annotations

import itertools
import random
import time
from decimal import Decimal
from typing import Any, Optional

from src.common.models import Rule
from src.rules.canon import canon_actions, canon_cond
from src.verify.engine import apply_actions, base, eval_cond, store, to_num


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


def input_fields(rules: list[Rule]) -> dict[str, dict[str, Any]]:
    """Business input name -> {source_name, kind, values} for every field tested by the rules."""
    fields: dict[str, dict[str, Any]] = {}
    for r in rules:
        for leaf in _leaves(r.conditions, []):
            f = fields.setdefault(leaf["field"], {"source_name": base(leaf["source_name"]), "values": set()})
            vals = leaf.get("value") if isinstance(leaf.get("value"), list) else [leaf.get("value")]
            for v in vals:
                if v is not None:
                    f["values"].add(v)
    for f in fields.values():
        f["kind"] = "number" if all(isinstance(v, (int, float)) for v in f["values"]) and f["values"] else "text"
        f["values"] = sorted(f["values"], key=lambda v: (str(type(v)), v))
    return fields


def output_fields(rules: list[Rule]) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in rules:
        for a in r.actions + r.else_actions:
            if a.type in ("set", "compute") and a.target:
                out.setdefault(a.target, base(a.source_name or a.target))
    return out


def _state(rules: list[Rule], inputs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    fields = input_fields(rules)
    state: dict[str, Any] = {}
    for business, value in inputs.items():
        src = fields.get(business, {}).get("source_name", base(business))
        n = to_num(value) if not isinstance(value, str) or fields.get(business, {}).get("kind") == "number" else None
        state[src] = n if n is not None else str(value).rstrip()
    return state, fields


def score(rules: list[Rule], inputs: dict[str, Any], defaults: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Apply rules in order; outputs start from the program's VALUE defaults."""
    t0 = time.perf_counter()
    state, _ = _state(rules, inputs)
    for name, value in (defaults or {}).items():
        state.setdefault(base(name), to_num(value) if isinstance(value, (int, float)) else value)
    fired: list[str] = []
    outputs = output_fields(rules)
    for r in rules:
        d = r.model_dump(by_alias=True)
        if eval_cond(d["conditions"], state):
            fired.append(r.rule_id)
            apply_actions(d["actions"], state, {})
        else:
            apply_actions(d["else_actions"], state, {})
    decision = {}
    for business, src in outputs.items():
        if src in state:
            v = state[src]
            decision[business] = float(v) if isinstance(v, Decimal) else v
    return {"decision": decision, "fired_rules": fired,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 3)}


# ---- SHAP ------------------------------------------------------------------------------

def _samples(fields: dict[str, dict[str, Any]], n: int, rng: random.Random) -> list[dict[str, Any]]:
    rows = []
    for _ in range(n):
        row = {}
        for name, f in fields.items():
            if f["kind"] == "number":
                vals = [float(v) for v in f["values"]] or [0.0]
                lo, hi = min(vals), max(vals)
                span = max(hi - lo, 1.0)
                if rng.random() < 0.5:
                    row[name] = rng.choice(vals) + rng.choice([-1, 0, 1]) * (span / 100 if span > 100 else 1)
                else:
                    row[name] = rng.uniform(max(0.0, lo - span), hi + span)
            else:
                row[name] = rng.choice(list(f["values"]) + ["?"])
        rows.append(row)
    return rows


def explain(rules: list[Rule], target: Optional[str] = None, n: int = 1500, seed: int = 7,
            defaults: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    from sklearn.ensemble import GradientBoostingClassifier

    fields = input_fields(rules)
    outputs = output_fields(rules)
    if not fields or not outputs:
        raise ValueError("rule set has no tested inputs or no outputs")
    target = target or next(iter(outputs))
    rng = random.Random(seed)
    rows = _samples(fields, n, rng)
    labels = [str(score(rules, row, defaults)["decision"].get(target)) for row in rows]
    names = list(fields)
    cats = {k: sorted({str(r[k]) for r in rows}) for k, f in fields.items() if f["kind"] == "text"}

    def vec(row: dict[str, Any]) -> list[float]:
        return [float(row[k]) if k not in cats else float(cats[k].index(str(row[k]))) for k in names]

    X = [vec(r) for r in rows]
    classes = sorted(set(labels))
    if len(classes) < 2:
        return {"target": target, "features": [{"name": k, "importance": 0.0} for k in names],
                "note": "the rules always give the same value on sampled inputs"}
    model = GradientBoostingClassifier(random_state=seed, n_estimators=60, max_depth=3).fit(X, labels)
    fidelity = sum(p == y for p, y in zip(model.predict(X), labels)) / len(labels)
    method = "shap.TreeExplainer"
    try:
        import numpy as np
        import shap
        sv = shap.TreeExplainer(model).shap_values(np.array(X))
        arr = np.abs(np.array(sv))
        # binary: (n, f); multiclass: (n, f, c) or list of (n, f)
        while arr.ndim > 2:
            arr = arr.mean(axis=-1) if arr.shape[-1] != len(names) else arr.mean(axis=0)
        importance = arr.mean(axis=0).tolist()
    except Exception:                                   # SHAP unavailable: impurity importance fallback
        importance = list(model.feature_importances_)
        method = "gradient boosting feature_importances_ (SHAP unavailable)"
    total = sum(importance) or 1.0
    feats = sorted(({"name": k, "importance": round(v / total, 4)} for k, v in zip(names, importance)),
                   key=lambda f: -f["importance"])
    return {"target": target, "classes": classes, "features": feats, "method": method,
            "surrogate": "GradientBoostingClassifier", "surrogate_fidelity": round(fidelity, 4),
            "samples": n, "note": "Surrogate of explicit rules; it summarises influence, it does not replace the rules."}


# ---- conflicts ---------------------------------------------------------------------------

def _by_business(c: dict[str, Any]) -> dict[str, Any]:
    """Condition keyed by business field names, so rules from different programs compare."""
    if c.get("all") is not None:
        return {"all": [_by_business(x) for x in c["all"]]}
    if c.get("any") is not None:
        return {"any": [_by_business(x) for x in c["any"]]}
    if c.get("not") is not None:
        return {"not": _by_business(c["not"])}
    return dict(c, source_name=c["field"])


def conflicts(rules: list[Rule], n: int = 400, seed: int = 7) -> list[dict[str, Any]]:
    """Duplicates (same logic), overlaps (both can fire) with conflicting writes to the same business field."""
    out: list[dict[str, Any]] = []
    keys = {r.rule_id: (canon_cond(r.conditions), canon_actions(r.actions)) for r in rules}
    for a, b in itertools.combinations(rules, 2):
        if keys[a.rule_id] == keys[b.rule_id]:
            out.append({"rules": [a.rule_id, b.rule_id], "kind": "duplicate", "reason": "identical condition and actions"})
    by_target: dict[str, list[tuple[Rule, Any]]] = {}
    for r in rules:
        for act in r.actions:
            if act.type == "set" and act.target and act.value is not None:
                by_target.setdefault(act.target, []).append((r, act.value))
    rng = random.Random(seed)
    for target, items in by_target.items():
        for (ra, va), (rb, vb) in itertools.combinations(items, 2):
            if ra.trace.program == rb.trace.program and ra.trace.paragraph == rb.trace.paragraph:
                continue                    # alternatives of one decision block are exclusive by design
            if str(va).rstrip() == str(vb).rstrip():
                continue
            pair = [ra, rb]
            fields = input_fields(pair)
            ca, cb = (_by_business(r.model_dump(by_alias=True)["conditions"]) for r in pair)
            both = 0
            for row in _samples(fields, n, rng):
                state = {base(k): (to_num(v) if fields[k]["kind"] == "number" else str(v)) for k, v in row.items()}
                if eval_cond(ca, state) and eval_cond(cb, state):
                    both += 1
                    example = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()}
                    break
            if both:
                out.append({"rules": [ra.rule_id, rb.rule_id], "kind": "conflict", "field": target,
                            "values": [va, vb], "example_inputs": example,
                            "reason": f"both can fire and set {target} to different values"})
    return out
