"""Rebuild the fine-tuned model from the committed LoRA adapter (base + adapter -> merged weights).

Runs on CPU in the training environment:

    .venv-train/Scripts/python scripts/merge_adapter.py        # -> models/cobol-enrich-1.5b/merged
    python scripts/serve_model.py                              # -> Ollama model cobol-enrich:1.5b

The base model is read from models/Qwen2.5-Coder-1.5B-Instruct when present,
otherwise downloaded from Hugging Face (Qwen/Qwen2.5-Coder-1.5B-Instruct, Apache-2.0).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_ID = "Qwen/Qwen2.5-Coder-1.5B-Instruct"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--adapter", default=str(ROOT / "weights" / "cobol-enrich-1.5b-lora"))
    local = ROOT / "models" / "Qwen2.5-Coder-1.5B-Instruct"
    ap.add_argument("--base", default=str(local) if local.exists() else BASE_ID)
    ap.add_argument("--out", default=str(ROOT / "models" / "cobol-enrich-1.5b" / "merged"))
    args = ap.parse_args()

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    adapter = Path(args.adapter)
    expected = (adapter / "adapter_model.safetensors.sha256").read_text().split()[0]
    actual = hashlib.sha256((adapter / "adapter_model.safetensors").read_bytes()).hexdigest()
    if actual != expected:
        print(f"checksum mismatch for adapter_model.safetensors: {actual} != {expected}")
        return 1
    print(f"adapter checksum ok ({actual[:12]}...), base: {args.base}")
    base = AutoModelForCausalLM.from_pretrained(args.base, dtype=torch.bfloat16)
    merged = PeftModel.from_pretrained(base, str(adapter)).merge_and_unload()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(out, safe_serialization=True)
    AutoTokenizer.from_pretrained(args.base).save_pretrained(out)
    print(f"merged model written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
