# COBOL-to-Decision Pipeline

> IBM Z Datathon · Real-Time AI for Critical Decisions

COBOL source in, decision model out. The pipeline reads COBOL programs and copybooks and finds every decision branch. It turns each branch into a structured, plain-English, source-traceable rule, then checks the rule against the **compiled COBOL program** before anyone relies on it. Everything runs on one machine: no customer data, no network calls.

```
COBOL + copybooks ─► parse & slice (exact) ─► AST rule ─► fine-tuned local LLM adds names & intent
                  ─► verify: symbolic check + differential test vs GnuCOBOL + self-consistency
                  ─► confidence & review queue ─► REST API / explorer UI / JSON · tree · PMML
```

**The LLM never decides logic.** Operators, values, actions and line ranges always come from the parser. The model only writes the title, intent, business field names and concepts (`src/llm/enrich.py: merge`).

## Results (measured, held-out test split)
<!-- RESULTS:START -->
Run `make eval` to produce `results/RESULTS.md`.
<!-- RESULTS:END -->

The test split uses **templates never seen in training**. All programs are synthetic (see [docs/limitations.md](docs/limitations.md)).

## Quick start (Windows + WSL, or Linux)
```bash
py -3.11 -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
wsl --install Ubuntu-24.04 && wsl -d Ubuntu-24.04 -- bash -c "apt-get update && apt-get install -y gnucobol"
ollama pull qwen2.5-coder:1.5b && ollama pull nomic-embed-text      # base model + search embeddings
.venv/Scripts/python scripts/load_demo.py --reset                     # extract the demo programs
.venv/Scripts/python -m uvicorn src.api.main:app --port 8000          # explorer at http://localhost:8000
```
Sign in with a token from `data/dev_tokens.json` (generated on first start; roles viewer, reviewer, admin).
On Linux, use `.venv/bin/python` and install GnuCOBOL with `apt install gnucobol`.

To reproduce the fine-tuned model (RTX 3050 6 GB laptop GPU, about 30 minutes):
```bash
make corpus finetune-data    # 300 programs, 809 gold rules, chat JSONL from the train split
make finetune serve-model    # LoRA on Qwen2.5-Coder-1.5B-Instruct, then import into Ollama as cobol-enrich:1.5b
make eval                    # every system on the test split -> results/RESULTS.md
```

## What is built
| Area | Where | Notes |
|---|---|---|
| Ingest: free/fixed format, `COPY ... REPLACING`, line mapping | `src/ingest/` | Every rule traces to its original file and line |
| Parser, data dictionary, 88-levels, abbreviated conditions | `src/analysis/` | Hand-written recursive descent; [subset and conventions](docs/parser_subset.md) |
| Decision slicer (IF, ELSE-IF, nested, EVALUATE with ALSO/THRU) | `src/analysis/slicer.py` | First-match semantics; GO TO, EXEC, SEARCH flagged, never guessed |
| AST-only baseline and canonical rule matching | `src/rules/` | `baseline_rule`, `canon`, `match` |
| Synthetic corpus: 6 domains, 18 templates, 300 programs | `src/corpus/`, `data/` | Gold rules with intents; splits held out by template; all compiled with GnuCOBOL |
| LLM enrichment, prompts with comments fenced as untrusted | `src/llm/` | Ollama, JSON-schema output, retry, baseline fallback |
| LoRA fine-tune + local serving | `scripts/finetune.py`, `scripts/serve_model.py` | Qwen2.5-Coder-1.5B-Instruct (Apache-2.0) |
| Verification: symbolic, differential vs compiled COBOL, consistency, naming | `src/verify/` | Weights from rules.md; routing to review |
| Rule store with versions, content hashes, audit log | `src/api/store.py` | SQLite |
| REST API per docs/api.md, bearer roles | `src/api/main.py` | `/docs` serves OpenAPI |
| Explorer UI (offline, no CDN) | `src/ui/static/` | Search, source highlighting, review, export, scoring, SHAP, conflicts |
| Exports: JSON, decision-tree JSON, PMML TreeModel | `src/rules/export.py` | Trees are tested to match rule semantics |
| Stateless `/score`, SHAP surrogate, conflict detection | `src/rules/decide.py` | Scoring inputs are never stored or logged |
| Evaluation harness | `scripts/evaluate.py` | AST-only, LLM-only, prompted, fine-tuned + verification, fault injection, latency |
| Packaging | `Dockerfile`, `docker-compose.yml`, `Makefile` | Model server on an internal (no-egress) network |

## Docs
[architecture](docs/architecture.md) · [requirements](docs/requirement.md) · [rules schema](docs/rules.md) · [API](docs/api.md) ·
[dataset](docs/dataset.md) · [evaluation](docs/evaluation.md) · [security](docs/security.md) · [demo script](docs/demo.md) ·
[parser subset](docs/parser_subset.md) · [limitations](docs/limitations.md) · [build plan](docs/implementation.md)

## Licences of third-party material
- `data/public/ibm-zopeneditor-sample/`: IBM Z Open Editor samples, Apache-2.0 (LICENSE included).
- Base model Qwen2.5-Coder-1.5B-Instruct: Apache-2.0. Weights are not in this repository.
