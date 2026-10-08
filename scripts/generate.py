"""Generate the synthetic COBOL corpus (implementation.md section 3).

Writes, per program:
  data/specs/<P>.yaml       sampled spec (the ground truth, incl. record layout)
  data/synthetic/<P>.cob    free-format program (+ <P>C.cpy copybook when used)
  data/gold/<P>.json        gold rules in the canonical schema
and data/splits.json (train/val/test by template). Every program is compiled
with GnuCOBOL into data/synthetic/bin/ and smoke-run; any failure fails the run.

    python scripts/generate.py --count 60
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.analysis.dictionary import parse_pic  # noqa: E402
from src.common.cobol import CompileJob, Toolchain, parse_display  # noqa: E402
from src.common.models import dump  # noqa: E402
from src.corpus.gold import gold_rules  # noqa: E402
from src.corpus.render import Renderer  # noqa: E402
from src.corpus.spec import sample_spec  # noqa: E402
from src.corpus.templates import DOMAIN_PREFIX, SPLIT_OF_TEMPLATE, TEMPLATES  # noqa: E402

DATA = ROOT / "data"
GENERATED = re.compile(rf"^({'|'.join(DOMAIN_PREFIX.values())})\d{{4}}")


def clean_previous() -> None:
    for folder in (DATA / "specs", DATA / "synthetic", DATA / "gold"):
        for f in folder.glob("*"):
            if f.is_file() and GENERATED.match(f.name):
                f.unlink()


def record_length(layout: list[dict]) -> int:
    return sum(parse_pic(f["pic"]).length for f in layout)


def generate(count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    numbers: Counter = Counter()
    programs = []
    for i in range(count):
        template = TEMPLATES[i % len(TEMPLATES)]
        numbers[template.domain] += 1
        prog_seed = rng.randrange(2**31)
        r = random.Random(prog_seed)
        spec = sample_spec(template, numbers[template.domain], r, prog_seed)
        renderer = Renderer(spec, r)
        rendered = renderer.render()
        gold = gold_rules(spec, rendered.spans, lambda b, v: renderer.lit(b, v, plain=True))
        spec["record_layout"] = rendered.record_layout
        spec["record_length"] = record_length(rendered.record_layout)
        spec["names88"] = rendered.names88
        spec["expected_rules"] = len(gold)
        prog = spec["program"]
        (DATA / "specs" / f"{prog}.yaml").write_text(yaml.safe_dump(spec, sort_keys=False, width=120), encoding="utf-8")
        (DATA / "synthetic" / spec["program_file"]).write_text(rendered.program_text, encoding="utf-8")
        if rendered.copybook_text is not None:
            (DATA / "synthetic" / spec["copybook"]).write_text(rendered.copybook_text, encoding="utf-8")
        split = SPLIT_OF_TEMPLATE[template.id]
        (DATA / "gold" / f"{prog}.json").write_text(json.dumps({
            "program": prog, "program_file": spec["program_file"], "domain": spec["domain"],
            "template": spec["template"], "split": split, "rules": [dump(g) for g in gold],
        }, indent=2), encoding="utf-8")
        programs.append({"program": prog, "file": spec["program_file"], "template": template.id,
                         "domain": template.domain, "split": split, "rules": len(gold),
                         "outputs": [o["name"] for o in spec["outputs"]], "record_length": spec["record_length"]})
    return programs


def write_splits(programs: list[dict]) -> dict:
    splits: dict = {"train": [], "val": [], "test": [], "reference": ["TXNCHK"]}
    for p in programs:
        splits[p["split"]].append(p["program"])
    splits["templates"] = {s: sorted({p["template"] for p in programs if p["split"] == s}) for s in ("train", "val", "test")}
    (DATA / "splits.json").write_text(json.dumps(splits, indent=2), encoding="utf-8")
    return splits


def compile_and_smoke(programs: list[dict]) -> list[str]:
    tc = Toolchain.detect()
    syn = DATA / "synthetic"
    jobs = [CompileJob(syn / p["file"], syn / "bin" / p["program"], [syn]) for p in programs]
    errors = [f"{r.source.name}: {r.output}" for r in tc.compile_many(jobs) if not r.ok]
    if errors:
        return errors
    runs = tc.run_batch([(syn / "bin" / p["program"], "0" * p["record_length"]) for p in programs])
    for p, raw in zip(programs, runs):
        out = parse_display(raw)
        missing = [o for o in p["outputs"] if o not in out]
        if missing:
            errors.append(f"{p['program']}: smoke run missing outputs {missing}")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20261006)
    ap.add_argument("--no-compile", action="store_true", help="skip the GnuCOBOL compile check")
    args = ap.parse_args()

    for d in ("specs", "synthetic", "gold"):
        (DATA / d).mkdir(parents=True, exist_ok=True)
    clean_previous()
    programs = generate(args.count, args.seed)
    splits = write_splits(programs)

    by_split = Counter(p["split"] for p in programs)
    by_domain = Counter(p["domain"] for p in programs)
    rules = Counter()
    for p in programs:
        rules[p["split"]] += p["rules"]
    print(f"generated {len(programs)} programs, {sum(rules.values())} gold rules")
    print("  by split :", {s: f"{by_split[s]} programs / {rules[s]} rules" for s in ("train", "val", "test")})
    print("  by domain:", dict(by_domain))
    print("  held-out templates:", {s: splits["templates"][s] for s in ("val", "test")})
    if args.no_compile:
        return 0
    errors = compile_and_smoke(programs)
    if errors:
        print(f"FAILED: {len(errors)} program(s) did not compile or run:")
        for e in errors:
            print("  " + e)
        return 1
    print(f"compiled and smoke-ran {len(programs)} programs with GnuCOBOL: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
