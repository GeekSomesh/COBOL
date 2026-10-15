# COBOL-to-Decision Pipeline

> IBM Z Datathon · Real-Time AI for Critical Decisions

COBOL source in, decision model out. The pipeline reads COBOL programs, copybooks and JCL, finds every decision branch, and turns each one into a structured, plain-English, source-traceable rule. Before anyone relies on a rule, it is checked against the **compiled COBOL program**. Everything runs on one machine: no customer data, no network calls.

```
COBOL + copybooks -> parse, slice, follow control & data flow (exact) -> AST rule
                  -> local LLM adds names & intent (never logic)
                  -> verify: SMT proof vs parse, run rules vs compiled GnuCOBOL, wording & number checks
                  -> calibrated confidence -> review (assign, comment, N approvers, four-eyes, audit chain)
                  -> API, explorer UI, CLI, SDK -> JSON, trees, PMML, DMN, scoring, impact, drift
```

**The LLM never decides logic.** Operators, values, actions and line ranges always come from the parser. The model only writes the title, intent, business field names and concepts (`src/llm/enrich.py: merge`).

## Results (measured)
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
- **The parser is what makes rules correct.** Every system built on the parser gets 1.000 rule F1, traceability and behavioural agreement on held-out templates. The same 7B model reading raw code matches 0 of 61 gold rules.
- **It holds on real code we did not write.** On IBM's SAM1/SAM2 samples the parser matches all 45 hand-drafted expected rules (drafted by an AI, not yet reviewed by a COBOL expert). SAM2, a called subprogram, agrees byte for byte with the compiled program on every generated input, and a one-line planted bug is caught.
- **Verification catches what a model or a bug could break.** Every behaviour-changing injected fault goes to review and no clean rule does. The remaining injected faults are proven by the SMT solver not to change any decision. Calibrated, the confidence score means what it says (calibration error 0.03).
- **Fine-tuning helps once the data is varied enough.** Trained on 12 templates it overfit and lost to prompting; trained on 21 templates (three more domains) it beats prompting the same 1.5B model on unseen templates and runs 3.7x faster than the 7B, which stays the most accurate and the default.

All corpus programs are synthetic; test templates are never seen in training. See [docs/limitations.md](docs/limitations.md) and [results/ERROR_ANALYSIS.md](results/ERROR_ANALYSIS.md).

## Quick start (Windows + WSL, or Linux)
```bash
py -3.11 -m venv .venv && .venv/Scripts/python -m pip install --require-hashes -r requirements.lock
wsl --install Ubuntu-24.04 && wsl -d Ubuntu-24.04 -- bash -c "apt-get update && apt-get install -y gnucobol"
ollama pull qwen2.5-coder:7b && ollama pull nomic-embed-text        # enrichment model + search embeddings
.venv/Scripts/python scripts/load_demo.py --reset                     # extract the demo programs
.venv/Scripts/python scripts/serve.py                                 # explorer at http://localhost:8000
```
Sign in with a token from `data/dev_tokens.json` (generated on first start; roles viewer, reviewer, admin).
On Linux, use `.venv/bin/python` and `apt install gnucobol`. Without WSL, GnuCOBOL can run in Docker (`make toolchain-image`, `COBOL_BACKEND=docker`). Without Ollama, everything works with AST-only names (`COBOL_LLM_MODEL=none`).

Command line, no server needed:
```bash
python -m src.cli extract data/public/ibm-zopeneditor-sample/SAM2.cbl --verify -o sam2.json
python -m src.cli export  data/public/ibm-zopeneditor-sample/SAM2.cbl --format dmn -o sam2.dmn
python -m src.cli repo    data/public/ibm-zopeneditor-sample --impact program:SAM2
```

Production: `docker compose up` (API + Ollama on an internal, no-egress network), TLS, OIDC, PostgreSQL, backups and monitoring are described in the [admin guide](docs/admin_guide.md). Day-to-day use: [user guide](docs/user_guide.md).

## Fine-tuning: what we did and what we found
Round 1: LoRA on Qwen2.5-Coder-1.5B-Instruct with 546 training rules from 12 templates (29 minutes on an RTX 3050 6 GB laptop GPU). It won on the validation templates but lost to prompting on the test templates: it had learned the style of its 12 templates. We reported that as a negative result.

Round 2: we added three domains (insurance claims, payroll, shipping; 9 templates, 54 programs) to the **training split only**, keeping validation and test unchanged, and retrained with the same settings (702 rules, 26 minutes). Selected on validation, then measured once on test with the same prompt as every other model: field names 0.590 vs 0.563 for the prompted 1.5B and 0.542 for round 1; intent 0.839 vs 0.837 and 0.818. The prompted 7B is still best (0.632 / 0.856) and stays the default; the fine-tuned model is the fast option and the fallback.

**The weights are in this repo** as LoRA adapters (71 MB each, with model cards and checksums): [round 2](weights/cobol-enrich-1.5b-v2-lora/README.md) and [round 1](weights/cobol-enrich-1.5b-lora/README.md). Merged models (2.9 GB) are rebuilt from base + adapter with `make weights serve-model`.

Reproduce from scratch (needs `.venv-train` from `requirements-train.txt` and llama.cpp's converter):
```bash
make corpus finetune-data    # 300 + 54 programs, gold rules, chat JSONL from the train split
make finetune serve-model    # LoRA, merge, GGUF Q8_0, import into Ollama (+ matched base)
make eval                    # every system and study -> results/RESULTS.md, README, evaluation.md
```

## What is built
| Area | Where | Notes |
|---|---|---|
| Ingest: free/fixed format, `COPY ... REPLACING`, line mapping | `src/ingest/` | Every rule traces to its original file and line |
| Parser, data dictionary, byte layout, 88-levels | `src/analysis/`, `src/verify/storage.py` | [Subset and conventions](docs/parser_subset.md) |
| Slicer, control and data flow, lookup tables | `src/analysis/slicer.py`, `cfg.py`, `tables.py` | Contexts from callers, derivations, dead code, SEARCH tables as per-row rules |
| Synthetic corpus: 9 domains, 27 templates, 354 programs | `src/corpus/`, `data/` | Splits held out by template; all compiled with GnuCOBOL |
| LLM enrichment, fine-tune, glossary | `src/llm/`, `scripts/finetune.py`, `src/rules/glossary.py` | Comments fenced as untrusted; schema-checked output |
| Verification | `src/verify/` | SMT structural proof, rule interpreter vs GnuCOBOL (programs and subprograms), wording and number checks, calibrated confidence |
| Repository analysis | `src/repo/` | JCL, call graph, datasets, impact analysis; rule drift in `src/rules/diff.py` |
| Rule store and review workflow | `src/api/store.py`, `src/api/db.py` | Versions, hash-chained audit, N approvers, four-eyes, comments, notifications; SQLite or PostgreSQL |
| REST API, explorer UI, CLI, SDK | `src/api/`, `src/ui/static/`, `src/cli.py`, `src/sdk.py` | OIDC, per-program access, rate limits, metrics, TLS; accessible UI |
| Exports and decisions | `src/rules/` | JSON, trees, PMML 4.4, DMN 1.3 (schema-validated), stateless scoring, SHAP, SMT conflict detection |
| Packaging and CI | `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml` | Hash-pinned dependencies, SBOM, non-root container |

## Docs
[user guide](docs/user_guide.md) · [admin guide](docs/admin_guide.md) · [architecture](docs/architecture.md) · [requirements status](docs/STATUS.md) ·
[rules schema](docs/rules.md) · [API](docs/api.md) · [dataset](docs/dataset.md) · [evaluation](docs/evaluation.md) · [security](docs/security.md) ·
[demo script](docs/demo.md) · [parser subset](docs/parser_subset.md) · [limitations](docs/limitations.md) · [roadmap](docs/roadmap.md) · [build plan](docs/implementation.md)

## Licences of third-party material
- `data/public/ibm-zopeneditor-sample/`: IBM Z Open Editor samples (programs, copybooks, JCL), Apache-2.0 (LICENSE included).
- `src/rules/schemas/`: PMML 4.4 schema (DMG) and DMN 1.3 schemas (OMG), published by their standards bodies.
- Base model Qwen2.5-Coder-1.5B-Instruct: Apache-2.0. Base weights are not in this repository; the LoRA adapters are.
