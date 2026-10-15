"""Score the parser on real code we did not write: IBM's SAM1/SAM2 samples.

The expected rules in data/gold_public/ are DRAFTS: written by hand from the
COBOL source and the documented slicing conventions (docs/parser_subset.md),
without looking at the parser's output, and not yet reviewed by a COBOL
expert. Scores here are indicative; disagreements are listed so a reviewer can
decide whether the parser or the draft is wrong.

    python scripts/eval_public.py            # prints the table, writes results/public_gold.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.models import Rule                      # noqa: E402
from src.pipeline import extract                        # noqa: E402
from src.rules.canon import canon_actions, canon_cond   # noqa: E402
from src.rules.match import match_rules, overlap        # noqa: E402

GOLD = ROOT / "data" / "gold_public"
SAMPLES = ROOT / "data" / "public" / "ibm-zopeneditor-sample"


def field_name(cobol: str) -> str:
    return cobol.split("(")[0].strip().lower().replace("-", "_")


def cond(c: list[Any]) -> dict[str, Any]:
    """[op, field, value | {ref}] / [all|any, ...] / [not, c] -> Cond dict."""
    head = c[0]
    if head in ("all", "any"):
        return {head: [cond(x) for x in c[1:]]}
    if head == "not":
        return {"not": cond(c[1])}
    leaf: dict[str, Any] = {"field": field_name(c[1]), "source_name": c[1], "op": head}
    if len(c) > 2:
        if isinstance(c[2], dict):
            leaf["ref"] = c[2]["ref"]
        else:
            leaf["value"] = c[2]
    return leaf


def wrap(c: dict[str, Any]) -> dict[str, Any]:
    return c if set(c) & {"all", "any", "not"} else {"all": [c]}


def action(a: list[Any]) -> dict[str, Any]:
    kind = a[0]
    if kind == "set":
        return {"type": "set", "source_name": a[1], "target": field_name(a[1]), "value": a[2]}
    if kind == "move":
        return {"type": "set", "source_name": a[1], "target": field_name(a[1]), "expr": a[2]}
    if kind == "compute":
        return {"type": "compute", "source_name": a[1], "target": field_name(a[1]), "expr": a[2]}
    if kind in ("perform", "call", "goto"):
        return {"type": kind, "source_name": a[1], "target": a[1]}
    if kind == "stop":
        return {"type": "stop", "source_name": "STOP RUN"}
    raise ValueError(f"unknown action {a}")


def load(program: str) -> tuple[dict[str, Any], list[Rule]]:
    doc = json.loads((GOLD / f"{program}.json").read_text(encoding="utf-8"))
    rules = []
    for n, r in enumerate(doc["rules"], start=1):
        rules.append(Rule.model_validate({
            "rule_id": f"{program}-G{n:03d}", "title": r.get("title", ""),
            "conditions": wrap(cond(r["when"])),
            "actions": [action(a) for a in r.get("then", [])],
            "else_actions": [action(a) for a in r.get("else", [])],
            "trace": {"program": doc["program_file"], "paragraph": r["paragraph"],
                      "line_start": r["lines"][0], "line_end": r["lines"][1]},
            "status": "needs_review",
            "provenance": {"source": "gold:hand-drafted", "verified": False},
        }))
    return doc, rules


def diff(pred: list[Rule], gold: list[Rule]) -> list[dict[str, Any]]:
    """Gold rules without an exact match, next to the closest prediction, and what differs."""
    key = lambda r: (canon_cond(r.conditions), canon_actions(r.actions), canon_actions(r.else_actions))  # noqa: E731
    pk = {p.rule_id: key(p) for p in pred}
    matched = {k for k in pk.values()}
    out = []
    for g in gold:
        gk = key(g)
        if gk in matched:
            continue
        near = max(pred, key=lambda p: overlap(p, g), default=None)
        what = []
        if near is not None:
            nk = pk[near.rule_id]
            what = [name for name, a, b in zip(("conditions", "actions", "else_actions"), gk, nk) if a != b]
        out.append({"gold": g.rule_id, "title": g.title, "lines": [g.trace.line_start, g.trace.line_end],
                    "closest": near.rule_id if near else None, "differs_in": what,
                    "gold_key": repr(gk)[:400], "pred_key": repr(pk[near.rule_id])[:400] if near else None})
    return out


def main() -> int:
    report: dict[str, Any] = {
        "note": "gold rules are hand-drafted and unverified; scores are indicative",
        "history": [
            {"date": "2026-10-15", "parser": "0.2.0", "f1": {"SAM1": 0.885, "SAM2": 0.947},
             "finding": "SAM1: 3 rules wrote ADD +1 as 'X + +1' (parser fixed). SAM2: 1 drafted trace "
                        "included END-IF against the documented convention (draft fixed)."}],
        "programs": {}}
    total = None
    print(f"{'program':<8}{'gold':>6}{'pred':>6}{'match':>7}{'P':>7}{'R':>7}{'F1':>7}")
    for program in ("SAM1", "SAM2"):
        doc, gold = load(program)
        pred = extract(SAMPLES / doc["program_file"]).rules
        m = match_rules(pred, gold)
        total = m if total is None else (total.add(m) or total)
        print(f"{program:<8}{m.n_gold:>6}{m.n_pred:>6}{m.tp:>7}{m.precision:>7.3f}{m.recall:>7.3f}{m.f1:>7.3f}")
        report["programs"][program] = {
            "gold": m.n_gold, "predicted": m.n_pred, "matched": m.tp, "logic_matched": m.logic_tp,
            "precision": round(m.precision, 4), "recall": round(m.recall, 4), "f1": round(m.f1, 4),
            "failures": dict(m.failures), "disagreements": diff(pred, gold)}
    report["overall"] = {"gold": total.n_gold, "predicted": total.n_pred, "matched": total.tp,
                         "precision": round(total.precision, 4), "recall": round(total.recall, 4),
                         "f1": round(total.f1, 4)}
    print(f"{'overall':<8}{total.n_gold:>6}{total.n_pred:>6}{total.tp:>7}{total.precision:>7.3f}"
          f"{total.recall:>7.3f}{total.f1:>7.3f}")
    for program, r in report["programs"].items():
        for d in r["disagreements"]:
            print(f"  {d['gold']} {d['title']!r} lines {d['lines']}: closest {d['closest']} differs in {d['differs_in']}")
    out = ROOT / "results" / "public_gold.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
