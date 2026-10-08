"""Run the pipeline over the corpus and score it against the gold rules.

Prints programs parsed, slices found, rules valid and rules matching gold
(implementation.md section 11), per split and domain, and writes
results/<system>.json. Phase 3 system: AST-only baseline.

    python scripts/run_corpus.py                 # all splits
    python scripts/run_corpus.py --split test
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.analysis.analyze import PARSER_VERSION  # noqa: E402
from src.common.models import Rule  # noqa: E402
from src.pipeline import extract  # noqa: E402
from src.rules.match import MatchResult, match_rules  # noqa: E402

DATA = ROOT / "data"


def load_gold(program: str) -> tuple[dict, list[Rule]]:
    doc = json.loads((DATA / "gold" / f"{program}.json").read_text(encoding="utf-8"))
    return doc, [Rule.model_validate(r) for r in doc["rules"]]


def naming_stats(pred: list[Rule], gold: list[Rule]) -> tuple[int, int]:
    """Leaves whose business ``field`` name equals the gold name (matched by COBOL name)."""
    def leaves(c, out):
        for k in ("all", "any"):
            for x in getattr(c, k, None) or []:
                leaves(x, out)
        if getattr(c, "not_", None) is not None:
            leaves(c.not_, out)
        if hasattr(c, "source_name"):
            out[c.source_name] = c.field
        return out
    gold_names: dict[str, str] = {}
    for g in gold:
        leaves(g.conditions, gold_names)
    hits = total = 0
    for p in pred:
        for src, fld in leaves(p.conditions, {}).items():
            if src in gold_names:
                total += 1
                hits += fld == gold_names[src]
    return hits, total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", choices=["train", "val", "test", "reference", "all"], default="all")
    ap.add_argument("--system", default="ast_only")
    ap.add_argument("--show-failures", type=int, default=5)
    args = ap.parse_args()

    splits = json.loads((DATA / "splits.json").read_text(encoding="utf-8"))
    names = ["reference", "train", "val", "test"] if args.split == "all" else [args.split]
    per_split: dict[str, MatchResult] = defaultdict(MatchResult)
    per_domain: dict[str, MatchResult] = defaultdict(MatchResult)
    stats = defaultdict(lambda: defaultdict(int))
    problems: list[str] = []
    started = time.perf_counter()

    for split in names:
        for program in splits[split]:
            doc, gold = load_gold(program)
            s = stats[split]
            s["programs"] += 1
            t0 = time.perf_counter()
            try:
                ex = extract(DATA / "synthetic" / doc["program_file"], [DATA / "synthetic"])
            except Exception as exc:                       # a crash is a result, not a stop
                problems.append(f"{program}: extraction failed: {exc}")
                per_split[split].add(match_rules([], gold))
                continue
            s["ms"] += (time.perf_counter() - t0) * 1000
            s["parsed"] += not ex.analysis.diagnostics
            s["slices"] += len(ex.analysis.slices)
            s["rules_valid"] += len(ex.rules)              # pydantic-validated Rule objects
            s["gold_rules"] += len(gold)
            s["count_ok"] += len(ex.analysis.slices) == len(gold)
            if ex.analysis.diagnostics:
                problems.append(f"{program}: diagnostics {ex.analysis.diagnostics}")
            if len(ex.analysis.slices) != len(gold):
                problems.append(f"{program}: {len(ex.analysis.slices)} slices, expected {len(gold)}")
            hits, total = naming_stats(ex.rules, gold)
            s["name_hits"] += hits
            s["name_total"] += total
            s["with_intent"] += sum(1 for r in ex.rules if r.intent)
            m = match_rules(ex.rules, gold)
            per_split[split].add(m)
            per_domain[doc["domain"]].add(m)
            for e in m.examples:
                problems.append(f"{program}: {e['gold']} {e['reason']} | gold {e['gold_key']} | pred {e['pred_key']}")

    header = f"{'split':<10}{'progs':>6}{'parsed':>7}{'count=':>7}{'slices':>7}{'gold':>6}{'match':>6}{'P':>7}{'R':>7}{'F1':>7}{'trace':>7}{'names':>7}"
    print(f"system: {args.system}   parser {PARSER_VERSION}")
    print(header)
    print("-" * len(header))
    report: dict = {"system": args.system, "parser_version": PARSER_VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "splits": {}, "domains": {}}
    total = MatchResult()
    for split in names:
        m, s = per_split[split], stats[split]
        total.add(m)
        name_acc = s["name_hits"] / s["name_total"] if s["name_total"] else 0.0
        print(f"{split:<10}{s['programs']:>6}{s['parsed']:>7}{s['count_ok']:>7}{s['slices']:>7}{s['gold_rules']:>6}"
              f"{m.tp:>6}{m.precision:>7.3f}{m.recall:>7.3f}{m.f1:>7.3f}{m.traceability:>7.3f}{name_acc:>7.3f}")
        report["splits"][split] = m.summary() | {
            "programs": s["programs"], "programs_parsed_clean": s["parsed"], "slice_count_matches": s["count_ok"],
            "slices": s["slices"], "rules_valid": s["rules_valid"], "field_name_accuracy": round(name_acc, 4),
            "rules_with_intent": s["with_intent"], "avg_ms_per_program": round(s["ms"] / max(s["programs"], 1), 1),
        }
    print("-" * len(header))
    print(f"{'overall':<10}{'':>34}{total.tp:>6}{total.precision:>7.3f}{total.recall:>7.3f}{total.f1:>7.3f}{total.traceability:>7.3f}")
    print("\nper domain F1:", {d: round(m.f1, 3) for d, m in sorted(per_domain.items())})
    report["overall"] = total.summary()
    report["domains"] = {d: m.summary() for d, m in sorted(per_domain.items())}
    report["problems"] = problems
    report["seconds"] = round(time.perf_counter() - started, 2)

    out = ROOT / "results" / f"{args.system}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {out.relative_to(ROOT)}  ({report['seconds']}s)")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems[: args.show_failures * 4]:
            print("  " + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
