"""Load the demo programs into the rule store (data/rules.db) through the full pipeline.

    python scripts/load_demo.py                         # LLM: best installed default model, else AST-only
    python scripts/load_demo.py --model none --reset    # AST-only, fresh database

Demo set: the TXNCHK reference program, programs from the held-out test
templates, a few training-template programs, and IBM's public SAM1/SAM2.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.api.store import Store, content_hash  # noqa: E402
from src.common.cobol import Toolchain, ToolchainError  # noqa: E402
from src.llm.client import OllamaClient, embed  # noqa: E402
from src.pipeline import PipelineConfig, run_pipeline  # noqa: E402
from src.rules.search import rule_text  # noqa: E402

DATA = ROOT / "data"
DEMO_TEMPLATES = ["credit_limit_assign", "interest_term", "penalty_repeat",       # held out (test split)
                  "fraud_minor_high_value", "fee_waiver_balance", "loan_age_term", "fraud_channel"]


def demo_programs() -> list[tuple[Path, list[Path]]]:
    progs: list[tuple[Path, list[Path]]] = [(DATA / "synthetic" / "TXNCHK.cob", [])]
    splits = json.loads((DATA / "splits.json").read_text())
    all_programs = splits["test"] + splits["val"] + splits["train"]
    for template in DEMO_TEMPLATES:
        for p in all_programs:
            doc = json.loads((DATA / "gold" / f"{p}.json").read_text())
            if doc["template"] == template:
                cpy = DATA / "synthetic" / f"{p}C.cpy"
                progs.append((DATA / "synthetic" / doc["program_file"], [cpy] if cpy.exists() else []))
                break
    pub = DATA / "public" / "ibm-zopeneditor-sample"
    progs += [(pub / "SAM1.cbl", [pub / "CUSTCOPY.cpy", pub / "TRANREC.cpy"]),
              (pub / "SAM2.cbl", [pub / "CUSTCOPY.cpy", pub / "TRANREC.cpy"])]
    return progs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="auto")
    ap.add_argument("--fewshot", type=int, default=0, help="only with an explicit --model")
    ap.add_argument("--consistency", type=int, default=2)
    ap.add_argument("--db", default=str(DATA / "rules.db"))
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    if args.reset and Path(args.db).exists():
        Path(args.db).unlink()
    store = Store(args.db)
    model, fewshot = args.model, args.fewshot
    if model == "auto":
        from src.api.main import DEFAULT_MODELS
        model, fewshot = next(((m, s) for m, s in DEFAULT_MODELS if OllamaClient(m).available()), ("none", 0))
    client = None if model == "none" else OllamaClient(model)
    try:
        toolchain = Toolchain.detect()
    except ToolchainError:
        toolchain = None
    print(f"model: {model}   toolchain: {toolchain.backend if toolchain else 'none'}")
    cfg = PipelineConfig(model=None if model == "none" else model, fewshot=fewshot,
                         consistency_runs=args.consistency if client else 0, cache_dir=DATA / "cache")
    for program, copybooks in demo_programs():
        t0 = time.perf_counter()
        files = {program.name: program.read_text(encoding="utf-8")}
        files.update({c.name: c.read_text(encoding="utf-8") for c in copybooks})
        pid = store.add_program(program.stem, program.name, files)
        ex = run_pipeline(program, [program.parent], cfg, toolchain, client)
        stats = store.save_rules(pid, ex.rules)
        store.set_program_status(pid, "extracted", ex.report | {"save": stats, "model": model,
                                                                "toolchain": toolchain.backend if toolchain else None})
        try:
            vecs = embed([rule_text(r) for r in ex.rules])
            for r, v in zip(ex.rules, vecs):
                store.put_embedding(r.rule_id, content_hash(r), v)
        except Exception:
            pass
        statuses = {}
        for r in ex.rules:
            statuses[r.status] = statuses.get(r.status, 0) + 1
        agree = ex.report.get("differential", {}).get("agreement", "n/a")
        print(f"{program.name:<14} {len(ex.rules):>3} rules {statuses}  COBOL agreement {agree}  "
              f"({time.perf_counter() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
