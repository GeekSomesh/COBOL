"""Evaluate an enrichment model on a split: names, intent, concepts, grounding.

    python scripts/enrich_eval.py --model qwen2.5-coder:7b --split val --fewshot 3
    python scripts/enrich_eval.py --model cobol-enrich:1.5b --split test --fewshot 0

Writes results/enrich_<label>_<split>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.common.models import Rule  # noqa: E402
from src.llm.client import OllamaClient, embed  # noqa: E402
from src.llm.enrich import enrich  # noqa: E402
from src.llm.evaluation import EnrichScores, gold_enrichment, name_map, pair  # noqa: E402
from src.llm.fewshot import fewshot_examples  # noqa: E402
from src.pipeline import extract  # noqa: E402

DATA = ROOT / "data"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--split", default="val", choices=["train", "val", "test", "reference"])
    ap.add_argument("--fewshot", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="max programs (0 = all)")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    client = OllamaClient(args.model)
    if not client.available():
        print(f"model {args.model} is not available in Ollama")
        return 1
    examples = fewshot_examples(args.fewshot)
    programs = json.loads((DATA / "splits.json").read_text())[args.split]
    if args.limit:
        programs = programs[: args.limit]
    scores = EnrichScores()
    started = time.perf_counter()
    for n, program in enumerate(programs, start=1):
        doc = json.loads((DATA / "gold" / f"{program}.json").read_text())
        gold = [Rule.model_validate(r) for r in doc["rules"]]
        ex = extract(DATA / "synthetic" / doc["program_file"], [DATA / "synthetic"])
        names = name_map(program)
        for i, j in pair(ex.rules, gold):
            s = ex.analysis.slices[i]
            t0 = time.perf_counter()
            rule, e = enrich(s, ex.rules[i], client, examples)
            scores.seconds += time.perf_counter() - t0
            scores.add(s, rule, e, gold_enrichment(s, gold[j], names))
        print(f"\r{n}/{len(programs)} programs, {scores.rules} rules, valid {scores.valid}", end="", flush=True)
    print()
    label = args.label or f"{args.model.replace(':', '-').replace('/', '-')}_fs{args.fewshot}"
    out = ROOT / "results" / f"enrich_{label}_{args.split}.json"
    meta = {"model": args.model, "fewshot": args.fewshot, "split": args.split,
            "programs": len(programs), "wall_seconds": round(time.perf_counter() - started, 1)}
    out.write_text(json.dumps(meta | scores.summary() | {"pairs": scores.intent_pairs}, indent=2), encoding="utf-8")
    try:
        scores.finish(embed)
    except Exception as exc:                 # keep the measured results even if embeddings fail
        print(f"intent similarity not computed: {exc}")
    summary = meta | scores.summary()
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "examples"}, indent=2))
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
