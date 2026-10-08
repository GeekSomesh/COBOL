"""Build chat-format JSONL for LoRA fine-tuning from the train (and val) splits.

Each example: system + user prompt for one slice (the same prompt used at
inference, without few-shot examples) -> assistant JSON with the gold title,
intent, business field names and concepts. Only the training split feeds the
training file, so validation and test templates stay unseen.

    python scripts/build_finetune_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.common.models import Rule  # noqa: E402
from src.llm.evaluation import gold_enrichment, name_map, pair  # noqa: E402
from src.llm.prompts import PROMPT_VERSION, messages  # noqa: E402
from src.pipeline import extract  # noqa: E402

DATA = ROOT / "data"


def build(split: str) -> list[dict]:
    rows = []
    for program in json.loads((DATA / "splits.json").read_text())[split]:
        doc = json.loads((DATA / "gold" / f"{program}.json").read_text())
        gold = [Rule.model_validate(r) for r in doc["rules"]]
        ex = extract(DATA / "synthetic" / doc["program_file"], [DATA / "synthetic"])
        names = name_map(program)
        for i, j in pair(ex.rules, gold):
            s = ex.analysis.slices[i]
            answer = gold_enrichment(s, gold[j], names)
            rows.append({"id": ex.rules[i].rule_id, "template": doc["template"],
                         "messages": messages(s) + [{"role": "assistant", "content": json.dumps(answer)}]})
    return rows


def main() -> int:
    out = DATA / "finetune"
    out.mkdir(exist_ok=True)
    for split in ("train", "val"):
        rows = build(split)
        with (out / f"{split}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        print(f"{split}: {len(rows)} examples from {len({r['template'] for r in rows})} templates")
    (out / "README.md").write_text(
        f"Chat-format fine-tuning data (prompt version {PROMPT_VERSION}), built by "
        "`scripts/build_finetune_data.py` from the synthetic corpus. train.jsonl uses only the "
        "train split; val.jsonl is for validation loss.\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
