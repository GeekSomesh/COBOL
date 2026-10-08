"""Few-shot examples for prompting, taken only from the training split."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.common.models import Rule, Slice
from src.llm.evaluation import gold_enrichment, name_map, pair

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
# Different structures and naming styles: plain IF, EVALUATE chain, nested IF.
EXAMPLE_TEMPLATES = ["fraud_minor_high_value", "interest_balance_tiers", "credit_bankruptcy"]


@lru_cache(maxsize=4)
def _examples(n: int) -> tuple[tuple[Slice, dict[str, Any]], ...]:
    from src.pipeline import extract

    if n <= 0:
        return ()
    splits = json.loads((DATA / "splits.json").read_text())
    out: list[tuple[Slice, dict[str, Any]]] = []
    for template in EXAMPLE_TEMPLATES[:n]:
        for program in splits["train"]:
            doc = json.loads((DATA / "gold" / f"{program}.json").read_text())
            if doc["template"] != template:
                continue
            gold = [Rule.model_validate(r) for r in doc["rules"]]
            ex = extract(DATA / "synthetic" / doc["program_file"], [DATA / "synthetic"])
            i, j = pair(ex.rules, gold)[0]
            out.append((ex.analysis.slices[i], gold_enrichment(ex.analysis.slices[i], gold[j], name_map(program))))
            break
    return tuple(out)


def fewshot_examples(n: int) -> list[tuple[Slice, dict[str, Any]]]:
    return list(_examples(n))
