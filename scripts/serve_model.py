"""Serve the fine-tuned enrichment model locally through Ollama.

Ollama 0.35 imports neither Qwen2 safetensors nor LoRA adapters, so the merged
model is converted to GGUF with llama.cpp's converter (Q8_0) and imported with
the chat template of the Ollama base model. The base model goes through the
same conversion (``--also-base``) so prompting-only and fine-tuned runs differ
only by the fine-tune.

    python scripts/serve_model.py                       # cobol-enrich:1.5b from models/cobol-enrich-1.5b/merged
    python scripts/serve_model.py --merged models/cobol-enrich-1.5b-B/merged --name cobol-enrich:1.5b-b

Needs llama.cpp's converter (sparse clone into models/tools/llama.cpp, see README)
and the training environment (.venv-train) for the conversion step.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAIN_PY = ROOT / ".venv-train" / "Scripts" / "python.exe"
CONVERTER = ROOT / "models" / "tools" / "llama.cpp" / "convert_hf_to_gguf.py"


def template_lines(base: str) -> list[str]:
    """TEMPLATE and PARAMETER lines of an Ollama model (multi-line blocks kept whole)."""
    mf = subprocess.run(["ollama", "show", base, "--modelfile"], capture_output=True, text=True,
                        encoding="utf-8", check=True).stdout
    out: list[str] = []
    keep = in_block = False
    for line in mf.splitlines():
        if in_block:                                   # inside a multi-line """ ... """ value
            if keep:
                out.append(line)
            if '"""' in line:
                in_block = False
            continue
        if not line.strip() or line.startswith("#"):
            continue
        keep = line.startswith(("TEMPLATE", "PARAMETER"))
        in_block = line.count('"""') == 1
        if keep:
            out.append(line)
    return out


def to_gguf(model_dir: Path, out: Path, outtype: str) -> None:
    if out.exists():
        return
    py = TRAIN_PY if TRAIN_PY.exists() else Path(sys.executable)
    subprocess.run([str(py), str(CONVERTER), str(model_dir), "--outfile", str(out), "--outtype", outtype], check=True)


def create(name: str, gguf: Path, base: str) -> None:
    mf = gguf.with_suffix(".Modelfile")
    mf.write_text("\n".join([f"FROM {gguf.resolve().as_posix()}"] + template_lines(base)) + "\n", encoding="utf-8")
    subprocess.run(["ollama", "create", name, "-f", str(mf)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    print(f"created {name} from {gguf.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--merged", default=str(ROOT / "models" / "cobol-enrich-1.5b" / "merged"))
    ap.add_argument("--name", default="cobol-enrich:1.5b")
    ap.add_argument("--base", default="qwen2.5-coder:1.5b", help="Ollama model whose chat template is reused")
    ap.add_argument("--outtype", default="q8_0")
    ap.add_argument("--also-base", default="", help="HF base model dir to import the same way (fair baseline)")
    args = ap.parse_args()
    if not CONVERTER.exists():
        print(f"llama.cpp converter not found at {CONVERTER}")
        return 1
    merged = Path(args.merged)
    gguf = merged.parent / f"model-{args.outtype}.gguf"
    to_gguf(merged, gguf, args.outtype)
    create(args.name, gguf, args.base)
    if args.also_base:
        base_gguf = ROOT / "models" / f"qwen2.5-coder-1.5b-instruct-{args.outtype}.gguf"
        to_gguf(Path(args.also_base), base_gguf, args.outtype)
        create(f"qwen-coder-base:1.5b-{args.outtype}", base_gguf, args.base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
