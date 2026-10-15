"""Rule drift: what changed between two extractions of a program (or two programs).

Rules are paired in three passes: identical logic (possibly moved to other lines),
then rules in the same paragraph with the most similar conditions and actions, and
the rest are added or removed. A changed pair lists the leaf-level differences
(threshold 740 -> 760, operator, added / dropped conditions, new action values)
and, when the SMT solver can find one, an input on which the two conditions disagree.
"""

from __future__ import annotations

from typing import Any, Optional

from src.common.models import Rule
from src.llm.prompts import describe_action, describe_cond
from src.rules.canon import canon_actions, rule_key
from src.rules.decide import _leaves


def _sig(r: Rule) -> tuple[set, set]:
    leaves = {(l["source_name"].upper(), l["op"]) for l in _leaves(r.conditions, [])}
    targets = {(a.type, (a.source_name or "").upper()) for a in r.actions + r.else_actions}
    return leaves, targets


def _similarity(a: Rule, b: Rule) -> float:
    (la, ta), (lb, tb) = _sig(a), _sig(b)
    jl = len(la & lb) / len(la | lb) if la | lb else 1.0
    jt = len(ta & tb) / len(ta | tb) if ta | tb else 1.0
    para = 0.2 if a.trace.paragraph == b.trace.paragraph else 0.0
    return 0.5 * jl + 0.3 * jt + para


def _leaf_changes(a: Rule, b: Rule) -> list[str]:
    def index(r: Rule) -> dict[tuple, list]:
        out: dict[tuple, list] = {}
        for l in _leaves(r.conditions, []):
            out.setdefault((l["source_name"].upper(), l["op"]), []).append(l.get("value", l.get("ref")))
        return out
    ia, ib = index(a), index(b)
    notes = []
    for key in sorted(set(ia) | set(ib), key=str):
        va, vb = ia.get(key), ib.get(key)
        if va is not None and vb is not None and sorted(map(str, va)) != sorted(map(str, vb)):
            notes.append(f"{key[0]} {key[1]}: {', '.join(str(x).rstrip() for x in va)} -> {', '.join(str(x).rstrip() for x in vb)}")
    fa = {k[0] for k in ia}
    fb = {k[0] for k in ib}
    for key in sorted(set(ia) - set(ib), key=str):
        if key[0] in fb:
            other = [k[1] for k in ib if k[0] == key[0]]
            notes.append(f"{key[0]}: operator {key[1]} -> {', '.join(other)}")
        else:
            notes.append(f"condition on {key[0]} removed")
    for key in sorted(set(ib) - set(ia), key=str):
        if key[0] not in fa:
            notes.append(f"condition on {key[0]} added")
    ca, cb = canon_actions(a.actions), canon_actions(b.actions)
    if ca != cb:
        notes.append("actions: " + "; ".join(describe_action(x.model_dump()) for x in a.actions)
                     + " -> " + "; ".join(describe_action(x.model_dump()) for x in b.actions))
    if canon_actions(a.else_actions) != canon_actions(b.else_actions):
        notes.append("else actions changed")
    return list(dict.fromkeys(notes))


def _witness(a: Rule, b: Rule) -> Optional[dict[str, Any]]:
    from src.verify import smt
    if not smt.available():
        return None
    try:
        same, example = smt.equivalent(a.model_dump(by_alias=True, exclude_none=True)["conditions"],
                                       b.model_dump(by_alias=True, exclude_none=True)["conditions"])
    except smt.Unsupported:
        return None
    return example if same is False else None


def diff_rules(old: list[Rule], new: list[Rule], threshold: float = 0.45) -> dict[str, Any]:
    ko = {r.rule_id: rule_key(r) for r in old}
    kn = {r.rule_id: rule_key(r) for r in new}
    unmatched_new = {r.rule_id: r for r in new}
    unchanged, moved = [], []
    rest_old = []
    for r in old:
        hit = next((n for n in unmatched_new.values() if kn[n.rule_id] == ko[r.rule_id]), None)
        if hit is None:
            rest_old.append(r)
            continue
        del unmatched_new[hit.rule_id]
        if (r.trace.line_start, r.trace.line_end) != (hit.trace.line_start, hit.trace.line_end):
            moved.append({"old": r.rule_id, "new": hit.rule_id,
                          "lines": [[r.trace.line_start, r.trace.line_end], [hit.trace.line_start, hit.trace.line_end]]})
        else:
            unchanged.append({"old": r.rule_id, "new": hit.rule_id})
    pairs = sorted(((_similarity(o, n), o.rule_id, n.rule_id) for o in rest_old for n in unmatched_new.values()),
                   reverse=True)
    used_o, used_n, changed = set(), set(), []
    by_id = {r.rule_id: r for r in old} | {("new", r.rule_id): r for r in new}
    for score, oid, nid in pairs:
        if score < threshold or oid in used_o or nid in used_n:
            continue
        used_o.add(oid)
        used_n.add(nid)
        o, n = by_id[oid], by_id[("new", nid)]
        changed.append({
            "old": oid, "new": nid, "similarity": round(score, 3),
            "paragraph": n.trace.paragraph,
            "before": {"when": describe_cond(o.model_dump(by_alias=True, exclude_none=True)["conditions"]),
                       "then": [describe_action(a.model_dump()) for a in o.actions]},
            "after": {"when": describe_cond(n.model_dump(by_alias=True, exclude_none=True)["conditions"]),
                      "then": [describe_action(a.model_dump()) for a in n.actions]},
            "changes": _leaf_changes(o, n),
            "example_where_they_differ": _witness(o, n),
            "was_approved": o.status == "approved",
        })
    removed = [{"rule_id": r.rule_id, "paragraph": r.trace.paragraph, "title": r.title,
                "was_approved": r.status == "approved"} for r in rest_old if r.rule_id not in used_o]
    added = [{"rule_id": r.rule_id, "paragraph": r.trace.paragraph,
              "when": describe_cond(r.model_dump(by_alias=True, exclude_none=True)["conditions"])}
             for r in unmatched_new.values() if r.rule_id not in used_n]
    return {"summary": {"unchanged": len(unchanged), "moved": len(moved), "changed": len(changed),
                        "added": len(added), "removed": len(removed),
                        "approved_rules_affected": sum(c["was_approved"] for c in changed) + sum(r["was_approved"] for r in removed)},
            "changed": changed, "added": added, "removed": removed, "moved": moved, "unchanged": unchanged}
