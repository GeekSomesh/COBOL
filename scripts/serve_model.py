"""Serve the fine-tuned enrichment model locally through Ollama.

Reuses the base model's chat template, points FROM at the merged LoRA weights,
and imports with the same quantization as the base model (q4_K_M) so the
fine-tuned and prompting-only models are compared like for like.

    python scripts/serve_model.py            # creates cobol-enrich:1.5b
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--merged", default=str(ROOT / "models" / "cobol-enrich-1.5b" / "merged"))
    ap.add_argument("--base", default="qwen2.5-coder:1.5b")
    ap.add_argument("--name", default="cobol-enrich:1.5b")
    ap.add_argument("--quantize", default="q4_K_M")
    args = ap.parse_args()

    merged = Path(args.merged).resolve()
    if not (merged / "config.json").exists():
        print(f"no merged model at {merged}; run scripts/finetune.py first")
        return 1
    base_mf = subprocess.run(["ollama", "show", args.base, "--modelfile"], capture_output=True, text=True,
                             encoding="utf-8", check=True).stdout
    lines, skip_license = [], False
    for line in base_mf.splitlines():
        if line.startswith("#"):
            continue
        if line.startswith("FROM "):
            lines.append(f"FROM {merged.as_posix()}")
            continue
        if line.startswith("SYSTEM "):
            continue                                   # the client sends its own system prompt
        if line.startswith("LICENSE"):
            skip_license = True
        if skip_license:
            if line.rstrip().endswith('"""') and not line.startswith('LICENSE """'):
                skip_license = False
            continue
        lines.append(line)
    text = "\n".join(lines).strip() + "\n"
    if not re.search(r'^PARAMETER stop "<\|im_end\|>"', text, re.M):
        text += 'PARAMETER stop "<|im_end|>"\n'
    text += ('LICENSE """Apache License 2.0. Base model: Qwen2.5-Coder-1.5B-Instruct (Apache-2.0). '
             'LoRA fine-tune on synthetic COBOL rule-enrichment data from this repository."""\n')
    mf = ROOT / "models" / "cobol-enrich.Modelfile"
    mf.write_text(text, encoding="utf-8")
    print(f"wrote {mf}")
    proc = subprocess.run(["ollama", "create", args.name, "-f", str(mf), "--quantize", args.quantize],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(proc.stdout[-2000:], proc.stderr[-2000:])
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
