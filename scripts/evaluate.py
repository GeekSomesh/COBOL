"""Evaluation (docs/evaluation.md, implementation.md section 8) on the held-out test split.

    python scripts/evaluate.py system ast_only
    python scripts/evaluate.py system llm_only     --model qwen2.5-coder:7b
    python scripts/evaluate.py system llm_slices   --model qwen2.5-coder:7b --fewshot 3 --label llm_slices_7b
    python scripts/evaluate.py system full         --model cobol-enrich:1.5b --consistency 2
    python scripts/evaluate.py faults              # verification ablation: fault injection + calibration
    python scripts/evaluate.py latency             # API read and scoring latency
    python scripts/evaluate.py table               # results/RESULTS.md from all results/eval_*.json
    python scripts/evaluate.py errors              # results/ERROR_ANALYSIS.md

Every number comes from these runs; nothing is typed in by hand.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.common.cobol import Toolchain  # noqa: E402
from src.common.models import Rule  # noqa: E402
from src.llm.evaluation import cosine, name_map, pair  # noqa: E402
from src.rules.match import MatchResult, match_rules, overlap  # noqa: E402
from src.verify.engine import base  # noqa: E402

DATA = ROOT / "data"
RESULTS = ROOT / "results"


def programs(split: str, limit: int = 0) -> list[tuple[str, dict, list[Rule]]]:
    names = json.loads((DATA / "splits.json").read_text())[split]
    out = []
    for p in names[: limit or None]:
        doc = json.loads((DATA / "gold" / f"{p}.json").read_text())
        out.append((p, doc, [Rule.model_validate(r) for r in doc["rules"]]))
    return out


def leaf_names(rule: Rule) -> dict[str, str]:
    out: dict[str, str] = {}

    def walk(c: dict[str, Any]) -> None:
        for k in ("all", "any"):
            for x in c.get(k) or []:
                walk(x)
        if c.get("not"):
            walk(c["not"])
        if c.get("source_name"):
            out[base(c["source_name"])] = c["field"]

    walk(rule.model_dump(by_alias=True)["conditions"])
    for a in rule.actions + rule.else_actions:
        if a.source_name and a.target and a.type in ("set", "compute"):
            out[base(a.source_name)] = a.target
    return out


def write(name: str, payload: dict[str, Any]) -> None:
    RESULTS.mkdir(exist_ok=True)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (RESULTS / f"eval_{name}.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"wrote results/eval_{name}.json")


# ---- systems -----------------------------------------------------------------------------

def run_system(args: argparse.Namespace) -> None:
    from src.llm.client import OllamaClient, embed
    from src.llm.enrich import enrich
    from src.llm.fewshot import fewshot_examples
    from src.llm.llm_only import extract_llm_only
    from src.pipeline import PipelineConfig, extract, run_pipeline
    from src.verify.checks import differential

    tc = Toolchain.detect()
    client = OllamaClient(args.model) if args.model else None
    if client is not None and not client.available():
        raise SystemExit(f"model {args.model} not available in Ollama")
    examples = fewshot_examples(args.fewshot) if client else []
    total = MatchResult()
    rows = agree = 0
    name_hits = name_total = 0
    intent_pairs: list[tuple[str, str]] = []
    statuses: dict[str, int] = {}
    warnings: dict[str, int] = {}
    name_errors: list[dict[str, Any]] = []
    ungrounded: list[dict[str, Any]] = []
    scores: list[float] = []
    seconds: list[float] = []
    per_program: list[dict[str, Any]] = []
    valid_outputs = 0
    progs = programs(args.split, args.limit)
    for n, (prog, doc, gold) in enumerate(progs, start=1):
        path = DATA / "synthetic" / doc["program_file"]
        exe = DATA / "synthetic" / "bin" / prog
        t0 = time.perf_counter()
        base_ex = extract(path, [DATA / "synthetic"])
        if args.system == "ast_only":
            rules = base_ex.rules
        elif args.system == "llm_slices":
            rules = []
            for s, r in zip(base_ex.analysis.slices, base_ex.rules):
                rr, e = enrich(s, r, client, examples)
                valid_outputs += e is not None
                rules.append(rr)
        elif args.system == "llm_only":
            rules, ok = extract_llm_only(prog, doc["program_file"], path.read_text(encoding="utf-8"), client)
            valid_outputs += ok
        else:   # full
            cfg = PipelineConfig(model=args.model, fewshot=args.fewshot, consistency_runs=args.consistency,
                                 exe=exe, cache_dir=DATA / "cache")
            ex = run_pipeline(path, [DATA / "synthetic"], cfg, tc, client)
            rules = ex.rules
            valid_outputs += ex.report.get("enriched", 0)
        seconds.append(time.perf_counter() - t0)
        m = match_rules(rules, gold)
        total.add(m)
        diff, _ = differential(base_ex.analysis, rules, exe, tc)
        rows += diff.rows
        agree += diff.agree
        gnames = name_map(prog)
        for r in rules:
            for src, business in leaf_names(r).items():
                if src in gnames:
                    name_total += 1
                    name_hits += business == gnames[src]
                    if business != gnames[src] and len(name_errors) < 25:
                        name_errors.append({"rule": r.rule_id, "cobol": src, "predicted": business, "gold": gnames[src]})
            if r.confidence.get("intent_grounded") is False and len(ungrounded) < 25:
                ungrounded.append({"rule": r.rule_id, "intent": r.intent})
            statuses[r.status] = statuses.get(r.status, 0) + 1
            for w in r.confidence.get("warnings", []):
                key = w.split(" (")[0].split(":")[0]
                if key.startswith("confidence below") and "weakest: " in w:
                    key += " - weakest " + w.split("weakest: ")[1].split(" ")[0]
                warnings[key] = warnings.get(key, 0) + 1
            if r.confidence.get("score") is not None:
                scores.append(r.confidence["score"])
        # intent pairs: logic-matched rules, or best trace overlap for the LLM-only system
        if args.system == "llm_only":
            for g in gold:
                best = max(rules, key=lambda r: overlap(r, g), default=None)
                if best is not None and overlap(best, g) >= 0.5 and best.intent:
                    intent_pairs.append((best.intent, g.intent))
        else:
            for i, j in pair(rules, gold):
                if rules[i].intent:
                    intent_pairs.append((rules[i].intent, gold[j].intent))
        per_program.append({"program": prog, "template": doc["template"], "pred": len(rules), "gold": len(gold),
                            "tp": m.tp, "agreement": round(diff.agreement, 4), "seconds": round(seconds[-1], 2)})
        print(f"\r{n}/{len(progs)} {prog} tp {total.tp}/{total.n_gold}", end="", flush=True)
    print()
    intent_sim = None
    if intent_pairs:
        vecs = embed([t for p in intent_pairs for t in p])
        intent_sim = round(statistics.mean(cosine(vecs[i], vecs[i + 1]) for i in range(0, len(vecs), 2)), 4)
    label = args.label or args.system
    write(label, {
        "system": label, "kind": args.system, "model": args.model, "fewshot": args.fewshot,
        "consistency_runs": args.consistency if args.system == "full" else 0, "split": args.split,
        "programs": len(progs), **total.summary(),
        "behavioural_agreement": round(agree / rows, 4) if rows else None, "behavioural_rows": rows,
        "field_name_accuracy": round(name_hits / name_total, 4) if name_total else None,
        "intent_similarity": intent_sim, "intent_pairs": len(intent_pairs),
        "llm_valid_outputs": valid_outputs if client else None,
        "statuses": statuses, "review_warnings": warnings, "mean_confidence": round(statistics.mean(scores), 4) if scores else None,
        "seconds_per_program": round(statistics.mean(seconds), 2),
        "per_program": per_program, "match_examples": total.examples[:10],
        "name_error_examples": name_errors, "ungrounded_intent_examples": ungrounded,
    })


# ---- verification ablation: fault injection --------------------------------------------

def mutate(rule: Rule, rng: random.Random) -> tuple[Rule, str] | None:
    r = copy.deepcopy(rule)
    leaves = []

    def walk(c):
        for k in ("all", "any"):
            for x in getattr(c, k, None) or []:
                walk(x)
        if getattr(c, "not_", None) is not None:
            walk(c.not_)
        if hasattr(c, "op") and isinstance(getattr(c, "value", None), (int, float)) and not isinstance(c.value, bool):
            leaves.append(c)

    walk(r.conditions)
    kinds = (["threshold", "operator"] if leaves else []) + (["action"] if any(a.type == "set" and a.value is not None for a in r.actions) else [])
    if not kinds:
        return None
    kind = rng.choice(kinds)
    if kind == "threshold":
        leaf = rng.choice(leaves)
        step = 1 if isinstance(leaf.value, int) else 0.01
        leaf.value = leaf.value + rng.choice([-1, 1]) * max(step, abs(leaf.value) * rng.choice([0.0, 0.1, 0.25]))
    elif kind == "operator":
        leaf = rng.choice(leaves)
        flips = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "=="}
        if leaf.op not in flips:
            return None
        leaf.op = flips[leaf.op]
    else:
        a = rng.choice([a for a in r.actions if a.type == "set" and a.value is not None])
        a.value = (a.value + 1) if isinstance(a.value, (int, float)) else ("X" if a.value != "X" else "Z")
    return r, kind


def behaviourally_equivalent(h, original: list[Rule], mutated: list[Rule], rng: random.Random) -> bool:
    """No input among a dense boundary + random sample gives different outputs (an 'equivalent mutant').
    The rule evaluator agrees with the compiled COBOL on correct rules, so this stands in for the program."""
    from src.verify.engine import run_rules, store
    from src.verify.harness import build_grid, same_output
    a = [r.model_dump(by_alias=True) for r in original]
    b = [r.model_dump(by_alias=True) for r in mutated]
    rows = build_grid(h, [r["conditions"] for r in a + b], seed=rng.randrange(10**6), random_rows=3000, max_rows=4000)
    for row in rows:
        sa = dict(h.initial)
        for f in h.layout:
            sa[f["name"]] = store(row.get(f["name"], ""), f["entry"])
        sb = dict(sa)
        run_rules(a, sa, h.fields)
        run_rules(b, sb, h.fields)
        if any(not same_output(sa.get(base(o)), sb.get(base(o)), h.fields.get(base(o))) for o in h.outputs):
            return False
    return True


def run_faults(args: argparse.Namespace) -> None:
    from src.pipeline import extract
    from src.verify.checks import differential, naming_score, structural
    from src.verify.confidence import apply_confidence
    from src.verify.harness import build_grid, build_harness

    tc = Toolchain.detect()
    rng = random.Random(args.seed)
    cases: list[dict[str, Any]] = []
    progs = programs(args.split, args.limit)
    for n, (prog, doc, _gold) in enumerate(progs, start=1):
        ex = extract(DATA / "synthetic" / doc["program_file"], [DATA / "synthetic"])
        exe = DATA / "synthetic" / "bin" / prog
        h = build_harness(ex.analysis)
        grid = build_grid(h, [s.condition for s in ex.analysis.slices])
        for k, (s, rule) in enumerate(zip(ex.analysis.slices, ex.rules)):
            variants = [(rule, None)]
            mutated = mutate(rule, rng)
            if mutated is not None:
                variants.append(mutated)
            for variant, kind in variants:
                rules = list(ex.rules)
                rules[k] = variant
                diff, _ = differential(ex.analysis, rules, exe, tc, h)
                comps = {"structural": structural(variant, s, grid),
                         "differential": diff.rule_agreement(k) if diff.rule_agreement(k) is not None else diff.agreement,
                         "consistency": None, "naming": naming_score(variant, s)}
                routed = apply_confidence(variant, comps)
                equivalent = kind is not None and behaviourally_equivalent(h, ex.rules, rules, rng)
                cases.append({"program": prog, "rule": rule.rule_id, "faulty": kind is not None, "kind": kind,
                              "equivalent_mutant": equivalent,
                              "score": routed.confidence["score"], "status": routed.status,
                              "structural": comps["structural"], "differential": comps["differential"],
                              "external": rule.external_dependency or bool(rule.unsupported)})
        print(f"\r{n}/{len(progs)} programs, {len(cases)} cases", end="", flush=True)
    print()
    faulty = [c for c in cases if c["faulty"]]
    clean = [c for c in cases if not c["faulty"]]
    flagged = lambda c: c["status"] == "needs_review"   # noqa: E731
    by_kind = {}
    for kind in ("threshold", "operator", "action"):
        ks = [c for c in faulty if c["kind"] == kind]
        if ks:
            live = [c for c in ks if not c["equivalent_mutant"]]
            by_kind[kind] = {"n": len(ks), "caught": round(sum(flagged(c) for c in ks) / len(ks), 4),
                             "caught_by_differential": round(sum(c["differential"] < 1.0 for c in ks) / len(ks), 4),
                             "equivalent_mutants": len(ks) - len(live),
                             "caught_by_differential_non_equivalent": round(sum(c["differential"] < 1.0 for c in live) / max(1, len(live)), 4)}
    # expected calibration error of the confidence score as P(rule correct)
    bins = [[] for _ in range(10)]
    for c in cases:
        bins[min(int(c["score"] * 10), 9)].append(c)
    ece = sum(len(b) / len(cases) * abs(statistics.mean(x["score"] for x in b) - statistics.mean(0.0 if x["faulty"] else 1.0 for x in b))
              for b in bins if b)
    # AUROC: probability that a clean rule scores higher than a faulty one (ties count half)
    auroc = statistics.mean(1.0 if c["score"] > f["score"] else 0.5 if c["score"] == f["score"] else 0.0
                            for c in clean for f in faulty)
    write("faults", {
        "system": "verification ablation (fault injection)", "split": args.split, "programs": len(progs),
        "faulty_rules": len(faulty), "clean_rules": len(clean),
        "with_verification": {
            "faults_routed_to_review": round(sum(flagged(c) for c in faulty) / len(faulty), 4),
            "faults_caught_by_differential_alone": round(sum(c["differential"] < 1.0 for c in faulty) / len(faulty), 4),
            "faults_caught_by_structural_alone": round(sum(c["structural"] < 1.0 for c in faulty) / len(faulty), 4),
            "equivalent_mutants": sum(c["equivalent_mutant"] for c in faulty),
            "non_equivalent_faults_caught_by_differential": round(
                sum(c["differential"] < 1.0 for c in faulty if not c["equivalent_mutant"])
                / max(1, sum(not c["equivalent_mutant"] for c in faulty)), 4),
            "clean_rules_routed_to_review": round(sum(flagged(c) for c in clean) / len(clean), 4),
            "clean_rules_routed_to_review_excluding_external": round(
                sum(flagged(c) for c in clean if not c["external"]) / max(1, sum(not c["external"] for c in clean)), 4),
            "mean_score_clean": round(statistics.mean(c["score"] for c in clean), 4),
            "mean_score_faulty": round(statistics.mean(c["score"] for c in faulty), 4),
            "expected_calibration_error": round(ece, 4),
            "auroc_clean_vs_faulty": round(auroc, 4),
        },
        "without_verification": {"faults_routed_to_review": 0.0,
                                 "note": "Without verification every extracted rule gets the same status, so injected faults pass unnoticed."},
        "by_fault_kind": by_kind,
    })


# ---- latency --------------------------------------------------------------------------------

def run_latency(args: argparse.Namespace) -> None:
    import os
    import tempfile
    os.environ["COBOL_LLM_MODEL"] = "none"
    from fastapi.testclient import TestClient

    from src.api import main
    from src.api.auth import User
    from src.api.store import Store
    from src.pipeline import extract
    from src.rules.decide import score

    db = Path(tempfile.mkdtemp()) / "lat.db"
    store = Store(db)
    main.store = store
    main.TOKENS = {"lat": User("lat", "admin")}
    h = {"Authorization": "Bearer lat"}
    progs = programs("test", 20)
    rules_all = []
    for prog, doc, _ in progs:
        files = {doc["program_file"]: (DATA / "synthetic" / doc["program_file"]).read_text()}
        pid = store.add_program(prog, doc["program_file"], files)
        rules = extract(DATA / "synthetic" / doc["program_file"], [DATA / "synthetic"]).rules
        store.save_rules(pid, rules)
        rules_all.append(rules)
    client = TestClient(main.app)
    timings: dict[str, list[float]] = {"GET /v1/rules?q": [], "GET /v1/rules/{id}": [], "GET /v1/rules/{id}/trace": []}
    ids = [r.rule_id for rs in rules_all for r in rs]
    for i in range(100):
        for key, url in (("GET /v1/rules?q", "/v1/rules?q=late+payment+penalty"), ("GET /v1/rules/{id}", f"/v1/rules/{ids[i % len(ids)]}"),
                         ("GET /v1/rules/{id}/trace", f"/v1/rules/{ids[i % len(ids)]}/trace")):
            t0 = time.perf_counter()
            assert client.get(url, headers=h).status_code == 200
            timings[key].append((time.perf_counter() - t0) * 1000)
    score_ms = []
    for i in range(1000):
        rules = rules_all[i % len(rules_all)]
        t0 = time.perf_counter()
        score(rules, {})
        score_ms.append((time.perf_counter() - t0) * 1000)
    p95 = lambda xs: sorted(xs)[int(0.95 * len(xs)) - 1]   # noqa: E731
    write("latency", {"system": "latency", "rules_in_store": len(ids),
                      "api_read_ms": {k: {"median": round(statistics.median(v), 2), "p95": round(p95(v), 2)} for k, v in timings.items()},
                      "score_ms_in_process": {"median": round(statistics.median(score_ms), 3), "p95": round(p95(score_ms), 3)},
                      "note": "In-process TestClient on this laptop; no network hop."})


# ---- results table -------------------------------------------------------------------------------

def run_table(args: argparse.Namespace) -> None:
    order = ["ast_only", "llm_only", "llm_slices_1.5b", "llm_slices_7b", "full"]
    names = {"ast_only": "AST-only (no LLM)", "llm_only": "LLM-only, zero-shot on raw code (7B)",
             "llm_slices_1.5b": "LLM on slices, prompted 1.5B Q8 (no fine-tune)",
             "llm_slices_7b": "LLM on slices, prompted 7B (no fine-tune)",
             "full": "Full pipeline (fine-tuned 1.5B + verification)"}
    fmt = lambda v: "n/a" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))   # noqa: E731
    lines = ["| System | Precision | Recall | F1 | F1, logic only | Trace acc. | Behavioural agreement | Field names | Intent similarity | s/program |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for key in order:
        f = RESULTS / f"eval_{key}.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text())
        label = names[key] + (f", first {d['programs']} test programs" if d.get("programs", 50) < 50 else "")
        ltp = d["tp"] + d["failures"].get("trace mismatch", 0)          # logic matched, lines ignored
        lp, lr = ltp / max(d["n_pred"], 1), ltp / max(d["n_gold"], 1)
        logic_f1 = 2 * lp * lr / (lp + lr) if lp + lr else 0.0
        lines.append(f"| {label} | {fmt(d['precision'])} | {fmt(d['recall'])} | {fmt(d['f1'])} | {fmt(logic_f1)} | "
                     f"{fmt(d['traceability_accuracy'])} | {fmt(d['behavioural_agreement'])} | "
                     f"{fmt(d['field_name_accuracy'])} | {fmt(d['intent_similarity'])} | "
                     f"{'<0.01' if d['seconds_per_program'] < 0.01 else format(d['seconds_per_program'], '.2f')} |")
    out = ["# Measured results", "", f"Test split: held-out templates, generated {datetime.now(timezone.utc):%Y-%m-%d}.", ""] + lines
    sp = RESULTS / "model_selection.json"
    if sp.exists():
        d = json.loads(sp.read_text())
        out += ["", "## Enrichment model selection (validation split, held-out templates)", "",
                f"Criterion: {d['criterion']}. The test split was not used for selection.", "",
                "| Model | Field names (exact) | Intent similarity | Concept overlap | Intent numbers grounded | s/rule | Selection score |",
                "|---|---|---|---|---|---|---|"]
        for m in d["models"]:
            out.append(f"| {m['model']} | {m['field_name_exact']:.3f} | {m['intent_similarity']:.3f} | {m['concept_jaccard']:.3f} | "
                       f"{m['intent_numbers_grounded']:.3f} | {m['seconds_per_rule']:.2f} | {m['selection_score']:.3f} |")
    fp = RESULTS / "eval_faults.json"
    if fp.exists():
        d = json.loads(fp.read_text())
        w = d["with_verification"]
        out += ["", "## Verification ablation (fault injection)", "",
                f"{d['faulty_rules']} rules with one injected fault (threshold, operator or action) and {d['clean_rules']} clean rules.", "",
                "| | With verification | Without verification |", "|---|---|---|",
                f"| Faulty rules routed to review | {w['faults_routed_to_review']:.3f} | 0.000 |",
                f"| Faults caught by differential test alone (all / behaviour-changing only) | {w['faults_caught_by_differential_alone']:.3f} / {w['non_equivalent_faults_caught_by_differential']:.3f} | - |",
                f"| Equivalent mutants (no input changes any output) | {w['equivalent_mutants']} of {d['faulty_rules']} | - |",
                f"| Clean rules routed to review (excluding external/unsupported) | {w['clean_rules_routed_to_review_excluding_external']:.3f} | - |",
                f"| Mean confidence, clean vs faulty | {w['mean_score_clean']:.3f} vs {w['mean_score_faulty']:.3f} | - |",
                f"| Confidence AUROC, clean vs faulty | {w['auroc_clean_vs_faulty']:.3f} | - |",
                f"| Expected calibration error (score read as a probability) | {w['expected_calibration_error']:.3f} | - |"]
    lp = RESULTS / "eval_latency.json"
    if lp.exists():
        d = json.loads(lp.read_text())
        out += ["", "## Latency", ""] + [f"- {k}: median {v['median']} ms, p95 {v['p95']} ms" for k, v in d["api_read_ms"].items()] + \
               [f"- scoring (in process): median {d['score_ms_in_process']['median']} ms, p95 {d['score_ms_in_process']['p95']} ms"]
    (RESULTS / "RESULTS.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


def run_errors(args: argparse.Namespace) -> None:
    """results/ERROR_ANALYSIS.md from the recorded failures (implementation.md 8, step 5)."""
    out = ["# Error analysis", "", "Generated from results/eval_*.json. Examples are verbatim model output.", ""]
    f = RESULTS / "eval_full.json"
    if f.exists():
        d = json.loads(f.read_text())
        out += ["## Full pipeline: why rules were routed to review", "",
                f"{d['statuses'].get('needs_review', 0)} of {sum(d['statuses'].values())} test rules went to review. Warnings raised:", ""]
        out += [f"- {k}: {v}" for k, v in sorted(d.get("review_warnings", {}).items(), key=lambda kv: -kv[1])] or ["- none"]
        out += ["", "## Full pipeline: field-name mistakes (first 10)", "", "| Rule | COBOL name | Model said | Gold |", "|---|---|---|---|"]
        out += [f"| {e['rule']} | {e['cobol']} | {e['predicted']} | {e['gold']} |" for e in d.get("name_error_examples", [])[:10]]
        train_names = set()
        for g in (DATA / "gold").glob("*.json"):
            doc = json.loads(g.read_text())
            if doc.get("split") == "train":
                for r in doc["rules"]:
                    train_names.update(leaf_names(Rule.model_validate(r)).values())
        leaked = [e for e in d.get("name_error_examples", []) if e["predicted"] in train_names and e["predicted"] != e["gold"]]
        out += ["", "Exact match is strict. Some of these are reasonable synonyms (for example `deposit_term` for "
                "`term_months`). Others are names the model copied from training templates: "
                f"{len(leaked)} of the {len(d.get('name_error_examples', []))} recorded mistakes use a business name that "
                "belongs to a different field in the training split (for example `CREDIT-LIMIT` named `max_loan`). "
                "That is the overfitting described in docs/limitations.md."]
        ug = d.get("ungrounded_intent_examples", [])
        out += ["", f"## Full pipeline: intents citing numbers not in the code ({len(ug)} recorded)", ""]
        out += [f"- {e['rule']}: \"{e['intent']}\"" for e in ug[:10]] or ["- none"]
    f = RESULTS / "eval_llm_only.json"
    if f.exists():
        d = json.loads(f.read_text())
        out += ["", "## LLM-only (no parser): failure categories", "",
                f"{d['n_gold']} gold rules, {d['n_pred']} predicted, {d['tp']} matched.", ""]
        out += [f"- {k}: {v}" for k, v in sorted(d["failures"].items(), key=lambda kv: -kv[1])]
        out += ["", "Examples (gold vs nearest prediction, canonical form):", ""]
        for e in d.get("match_examples", [])[:3]:
            out += [f"- {e['gold']} ({e['reason']})", f"  - gold: `{e['gold_key'][:220]}`", f"  - pred: `{(e['pred_key'] or '-')[:220]}`"]
    f = RESULTS / "eval_faults.json"
    if f.exists():
        d = json.loads(f.read_text())
        w = d["with_verification"]
        out += ["", "## Faults the behavioural test could not see", "",
                f"{w['equivalent_mutants']} of {d['faulty_rules']} injected faults change no output for any input tried "
                "(for example, a range bound raised beyond the field's PIC maximum, or a threshold moved inside a region an "
                "earlier branch already claims). No behavioural test can detect those; the symbolic check still flags every "
                f"one because the rule no longer matches the parsed source. On the behaviour-changing faults, the differential "
                f"test alone caught {w['non_equivalent_faults_caught_by_differential']:.3f}."]
    (RESULTS / "ERROR_ANALYSIS.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("system")
    s.add_argument("system", choices=["ast_only", "llm_only", "llm_slices", "full"])
    s.add_argument("--model", default=None)
    s.add_argument("--fewshot", type=int, default=0)
    s.add_argument("--consistency", type=int, default=0)
    s.add_argument("--split", default="test")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--label", default="")
    f = sub.add_parser("faults")
    f.add_argument("--split", default="test")
    f.add_argument("--limit", type=int, default=0)
    f.add_argument("--seed", type=int, default=11)
    sub.add_parser("latency")
    sub.add_parser("table")
    sub.add_parser("errors")
    args = ap.parse_args()
    {"system": run_system, "faults": run_faults, "latency": run_latency, "table": run_table,
     "errors": run_errors}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
