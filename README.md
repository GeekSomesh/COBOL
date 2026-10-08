# COBOL-to-Decision Pipeline

Extract structured, source-traceable decision rules from COBOL programs.
Design docs are in [docs/](docs/README.md); the build plan is [docs/implementation.md](docs/implementation.md).

**Status:** phases 0 to 3 are done (setup, parser and slicer, synthetic corpus, AST-only baseline).
LLM enrichment, verification, API/UI and evaluation come next.

## Setup (Windows + WSL, or Linux)
```bash
py -3.11 -m venv .venv                    # Linux: python3.11 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
wsl --install Ubuntu-24.04                # Linux: skip
wsl -d Ubuntu-24.04 -- bash -c "apt-get update && apt-get install -y gnucobol"
```
`src/common/cobol.py` finds `cobc` on PATH (Linux) or inside WSL
(`COBOL_BACKEND=native|wsl`, `COBOL_WSL_DISTRO`, default `Ubuntu-24.04`).

## Commands
```bash
.venv/Scripts/python -m pytest                         # 60 tests; GnuCOBOL tests skip if cobc is missing
.venv/Scripts/python scripts/generate.py --count 60   # corpus + gold + splits, compiled with GnuCOBOL
.venv/Scripts/python scripts/run_corpus.py            # AST-only baseline vs gold -> results/ast_only.json
```

Extract rules from one program in Python:
```python
from src.pipeline import extract
ex = extract("data/synthetic/TXNCHK.cob", ["data/synthetic"])
print(ex.rules[0].model_dump_json(indent=2, by_alias=True, exclude_none=True))
```

## Layout
| Path | What |
|---|---|
| `src/common/models.py` | Frozen schema: `Rule`, `Slice`, `Cond`, `Leaf`, `Action`, `Trace` |
| `src/common/cobol.py` | GnuCOBOL compile/run (native or WSL) |
| `src/ingest/` | Source loading, free/fixed format, COPY ... REPLACING with line mapping |
| `src/analysis/` | Lexer, parser, data dictionary, decision slicer ([subset and conventions](docs/parser_subset.md)) |
| `src/rules/` | AST baseline extractor, canonical forms, rule matching |
| `src/corpus/` | Synthetic generator: domain templates, spec sampling, rendering, gold rules |
| `data/synthetic/`, `data/gold/`, `data/specs/`, `data/splits.json` | Generated corpus (reproducible with `generate.py`) |
| `data/public/` | Public sample programs (IBM, Apache-2.0) |
