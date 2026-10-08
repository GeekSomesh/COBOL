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
Test split: held-out templates, generated 2026-10-08.

| System | Precision | Recall | F1 | F1, logic only | Trace acc. | Behavioural agreement | Field names | Intent similarity | s/program |
|---|---|---|---|---|---|---|---|---|---|
| AST-only (no LLM) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.192 | n/a | <0.01 |
| LLM-only, zero-shot on raw code (7B), first 20 test programs | 0.000 | 0.000 | 0.000 | 0.183 | 0.000 | 0.621 | 0.261 | 0.775 | 25.24 |
| LLM on slices, prompted 1.5B Q8 (no fine-tune) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.566 | 0.835 | 3.28 |
| LLM on slices, prompted 7B (no fine-tune) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.632 | 0.856 | 10.95 |
| Full pipeline (fine-tuned 1.5B + verification) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.542 | 0.820 | 10.82 |

#### Enrichment model selection (validation split, held-out templates)

Criterion: mean of field_name_exact, intent_similarity, concept_jaccard. The test split was not used for selection.

| Model | Field names (exact) | Intent similarity | Concept overlap | Intent numbers grounded | s/rule | Selection score |
|---|---|---|---|---|---|---|
| fine-tuned A (2 epochs, lr 2e-4) - selected | 0.607 | 0.795 | 0.474 | 0.955 | 1.23 | 0.626 |
| fine-tuned B (0.6 epoch, lr 1e-4) | 0.507 | 0.802 | 0.397 | 0.946 | 1.10 | 0.569 |
| base 1.5B Q8_0, 3-shot | 0.476 | 0.786 | 0.327 | 0.982 | 1.23 | 0.529 |
| base 1.5B Q4_K_M (Ollama), 3-shot | 0.444 | 0.784 | 0.330 | 0.991 | 2.09 | 0.519 |
| base 7B Q4_K_M (Ollama), 3-shot | 0.522 | 0.822 | 0.453 | 1.000 | 4.71 | 0.599 |

#### Verification ablation (fault injection)

151 rules with one injected fault (threshold, operator or action) and 151 clean rules.

| | With verification | Without verification |
|---|---|---|
| Faulty rules routed to review | 1.000 | 0.000 |
| Faults caught by differential test alone (all / behaviour-changing only) | 0.861 / 1.000 | - |
| Equivalent mutants (no input changes any output) | 21 of 151 | - |
| Clean rules routed to review (excluding external/unsupported) | 0.000 | - |
| Mean confidence, clean vs faulty | 0.968 vs 0.637 | - |
| Confidence AUROC, clean vs faulty | 0.952 | - |
| Expected calibration error (score read as a probability) | 0.302 | - |

#### Latency

- GET /v1/rules?q: median 24.14 ms, p95 47.51 ms
- GET /v1/rules/{id}: median 3.29 ms, p95 4.84 ms
- GET /v1/rules/{id}/trace: median 3.2 ms, p95 4.46 ms
- scoring (in process): median 0.052 ms, p95 0.117 ms
<!-- RESULTS:END -->

**What the numbers say**
- **The parser is what makes rules correct.** Every system that builds on the parser gets 1.000 rule F1, traceability and behavioural agreement on held-out templates. The same 7B model reading raw code matches 0 of 61 gold rules. It treats 88-level names as fields, drops first-match exclusions and cites wrong lines, and its rules agree with the compiled program on only 62% of inputs.
- **Verification catches what the LLM or a bug could break.** Every injected fault is routed to review, and no clean rule is. The differential test against the compiled COBOL alone catches every behaviour-changing fault; the rest are equivalent mutants that no test can see.
- **Naming and intent are where models differ.** Fine-tuning won on validation but not on the unseen test templates (it overfit), so the explorer defaults to the prompted 7B. Details: [results/ERROR_ANALYSIS.md](results/ERROR_ANALYSIS.md), [docs/limitations.md](docs/limitations.md).

The test split uses **templates never seen in training**. All programs are synthetic (see [docs/limitations.md](docs/limitations.md)).

## Quick start (Windows + WSL, or Linux)
```bash
py -3.11 -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
wsl --install Ubuntu-24.04 && wsl -d Ubuntu-24.04 -- bash -c "apt-get update && apt-get install -y gnucobol"
ollama pull qwen2.5-coder:7b && ollama pull nomic-embed-text        # enrichment model + search embeddings
.venv/Scripts/python scripts/load_demo.py --reset                     # extract the demo programs
.venv/Scripts/python -m uvicorn src.api.main:app --port 8000          # explorer at http://localhost:8000
```
Sign in with a token from `data/dev_tokens.json` (generated on first start; roles viewer, reviewer, admin).
On Linux, use `.venv/bin/python` and install GnuCOBOL with `apt install gnucobol`. Without Ollama, everything still works with AST-only names (`COBOL_LLM_MODEL=none`).
Container: `docker build -t cobol-to-decision .`, then run it with `COBOL_API_TOKENS="token:admin:you"`. GnuCOBOL is inside the image.

## Fine-tuning: what we did and what we found
We fine-tuned Qwen2.5-Coder-1.5B-Instruct with LoRA on the 546 training rules. That took 29 minutes on an RTX 3050 6 GB laptop GPU. We picked the best of two runs on the **validation** templates, where it clearly beat the same base model prompted with three examples.

On the **test** templates it did not hold up. It names fields and writes intents slightly worse than the prompted 1.5B and the prompted 7B (tables below), because it learned the style of the 12 training templates. So we report it as a measured negative result, not a win.

The explorer defaults to the prompted 7B, the best measured model; the fine-tuned 1.5B is about 3x faster and one setting away (`COBOL_LLM_MODEL=cobol-enrich:1.5b`). The parser always owns the logic, so rule structure, traces and behavioural agreement are identical across models.

To reproduce (needs `.venv-train` from `requirements-train.txt`, plus llama.cpp's converter as described there):
```bash
make corpus finetune-data    # 300 programs, 809 gold rules, chat JSONL from the train split
make finetune serve-model    # LoRA, merge, GGUF Q8_0, import into Ollama as cobol-enrich:1.5b (+ matched base)
make eval                    # every system on the test split -> results/RESULTS.md, README, evaluation.md
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
