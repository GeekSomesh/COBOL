# Requirements status (as built)

Each requirement from [requirement.md](requirement.md), where it lives, and what checks it.
Status: **Built** works in the demo and is tested. **Partial** works with stated limits. **Not built** is planned.

## Functional
| ID | Requirement | Status | Where | Checked by |
|---|---|---|---|---|
| FR-01 | Accept programs and copybooks | Built | `POST /v1/programs`, `src/ingest/source.py` | `tests/test_api.py` |
| FR-02 | Resolve COPY incl. REPLACING, map lines | Built | `src/ingest/source.py` | `tests/test_ingest.py` |
| FR-03 | Parse DATA and PROCEDURE DIVISION | Built (documented subset) | `src/analysis/parser.py` | all 354 corpus programs + IBM SAM1/SAM2/SAM1LIB parse with no diagnostics; 45/45 drafted expected rules on IBM code |
| FR-04 | Data dictionary (PIC, USAGE, REDEFINES, OCCURS, 88) | Built | `src/analysis/dictionary.py` | `tests/test_dictionary.py` |
| FR-05 | Control-flow graph | Built | `src/analysis/cfg.py` (PERFORM THRU/SECTION/loops, GO TO, fall-through, contexts, derivations, dead code) | `tests/test_flow.py`; interpreter agrees with compiled FLOWPRG |
| FR-06 | Find IF / ELSE-IF / EVALUATE decisions | Built | `src/analysis/slicer.py` | `tests/test_analysis.py` |
| FR-07 | Slice controlled statements | Built | `src/analysis/slicer.py` | `tests/test_analysis.py` |
| FR-08 | Intent + structured rule per slice | Built | `src/rules/baseline.py`, `src/llm/enrich.py` | schema-valid output on 100% of test rules |
| FR-09 | 88-levels and abbreviated conditions | Built | `src/analysis/parser.py` | `tests/test_analysis.py` |
| FR-10 | Flag external dependencies | Built | slicer (`CALL`, `EXEC` with statement text) | `tests/test_analysis.py` |
| FR-11 | Symbolic check against the AST | Built (SMT-proven equivalence, z3) | `src/verify/checks.py: structural`, `src/verify/smt.py` | `tests/test_smt.py`, fault injection |
| FR-12 | Differential testing vs compiled COBOL | Built: ACCEPT/DISPLAY programs and CALLed subprograms (generated driver, byte-level records); file-processing main programs not run | `src/verify/harness.py`, `interpreter.py`, `subprogram.py`, `storage.py` | `tests/test_subprogram.py` (IBM SAM2 427/427), `results/eval_real.json` |
| FR-13 | Confidence with breakdown | Built, calibrated (`p_correct`) | `src/verify/confidence.py`, `data/calibration.json` | ECE 0.303 -> 0.032 on held-out fault cases (`results/eval_calibration.json`) |
| FR-14 | Review workflow with audit | Built: assignment, comments, N approvers, four-eyes, bulk, notifications, hash-chained log | `src/api/store.py`, `src/api/main.py` | `tests/test_api.py`, `tests/test_postgres.py` |
| FR-15 | Source traceability | Built | `Trace` on every rule | traceability accuracy 1.000 on the test split |
| FR-16 | Business-concept search | Built | `src/rules/search.py` | `tests/test_export_search.py` |
| FR-17 | REST API with OpenAPI | Built | `src/api/main.py`, `/openapi.json`, `/docs` | `tests/test_api.py` |
| FR-18 | JSON and decision-tree export | Built | `src/rules/export.py` | trees replay identically to rules (`tests/test_export_search.py`) |
| FR-19 | PMML export (+ DMN 1.3) | Built: PMML for single-output blocks, DMN for all blocks; both validated against the official XSDs | `src/rules/export.py`, `src/rules/dmn.py`, `src/rules/schema_check.py` | 114/114 exportable corpus programs valid PMML; DMN valid for every program |
| FR-20 | Explorer with side-by-side source | Built | `src/ui/static/` | checked in a browser (search, highlight, review, export, score, SHAP, audit) |
| FR-21 | SHAP feature influence | Built | `src/rules/decide.py: explain` | `tests/test_export_search.py` |
| FR-22 | Duplicate / overlap / conflict detection | Built (exact, SMT; sampling as fallback) | `src/rules/decide.py: conflicts` | `tests/test_smt.py`, `tests/test_export_search.py` |
| FR-23 | Stateless scoring with approved rules | Built | `POST /v1/score` | `tests/test_api.py` |

## Non-functional
| ID | Requirement | Status | Evidence |
|---|---|---|---|
| NFR-01 | Source in, decision model out; zero egress | Built | Local Ollama + local GnuCOBOL; compose puts the model server on an internal network |
| NFR-02 | No customer data | Built | Synthetic corpus and public samples only |
| NFR-03 | F1 >= 0.90, behavioural agreement >= 0.95 | Met on the synthetic test split | `results/RESULTS.md` (see limitations for scope) |
| NFR-04 | Exact source lines | Built | Trace accuracy 1.000 |
| NFR-05 | Minutes per program, reads < 200 ms, scoring < 50 ms | Met | `results/eval_latency.json` |
| NFR-06 | Reproducible | Built | Fixed seeds, temperature 0, rule IDs stable, unchanged code keeps rule versions |
| NFR-07 | Containerised | Built | Image builds from the hash-locked requirements, runs as non-root with a health check; smoke test 2026-10-16: RATETAB 30/30 and SAM2 427/427 differential agreement inside the container; compose (API + Ollama on an internal network) run, see below |
| NFR-08 | Usable without COBOL knowledge | Built | Business names, intents, concept search |
| NFR-09 | All review actions logged | Built | `reviews` table (hash chain), `/v1/audit`, `/v1/audit/verify` |

## Beyond the requirements (built 2026-10-16)
| Capability | Where | Checked by |
|---|---|---|
| Lookup tables: SEARCH over VALUE tables becomes one rule per row | `src/analysis/tables.py` | `tests/test_tables.py` (agrees with compiled program) |
| Repository analysis: call graph, copybooks, JCL jobs, datasets, findings | `src/repo/` | `tests/test_repo.py` on IBM's JCL and programs |
| Impact analysis (field, copybook, program, dataset) | `src/repo/graph.py: impact`, `GET /v1/impact` | `tests/test_repo.py`, `tests/test_api.py` |
| Rule drift between versions, with a distinguishing input | `src/rules/diff.py`, `GET /v1/diff` | `tests/test_repo.py`, `tests/test_api.py` |
| Intent wording check (comparisons) | `src/llm/evaluation.py: comparison_issues` | 0 false alarms on 965 gold intents, 97% of flipped wordings caught |
| Impossible-rule detection (SMT) | `src/verify/checks.py: satisfiable` | `tests/test_smt.py` |
| Business glossary | `src/rules/glossary.py`, `/v1/glossary` | `tests/test_glossary.py` |
| OIDC single sign-on, per-program access | `src/api/auth.py`, `src/api/access.py` | `tests/test_ops_security.py` |
| PostgreSQL store | `src/api/db.py` | `tests/test_postgres.py` (WSL PostgreSQL 16; CI service container) |
| TLS, security headers, rate limits, metrics, JSON logs | `scripts/serve.py`, `src/api/ops.py` | `tests/test_ops_security.py`; TLS smoke test with a dev certificate |
| Backups (verified) and retention | `Store.backup/purge`, `scripts/backup.py`, `/v1/admin/*` | `tests/test_ops_security.py` |
| Hash-pinned dependencies, SBOM | `requirements.lock`, `sbom.cdx.json`, `scripts/lock_deps.py` | container build installs with `--require-hashes` |
| Toolchain backends: native, WSL, Docker | `src/common/cobol.py`, `docker/toolchain.Dockerfile` | toolchain tests pass on the Docker backend |
| CLI and Python client | `src/cli.py`, `src/sdk.py` | `tests/test_cli_sdk_perf.py` |
| Linear-time analysis of large programs | `scripts/perf.py` | 51,571 lines in about 6 s (`results/perf.json`); regression test |
| CI | `.github/workflows/ci.yml` | tests with native GnuCOBOL and PostgreSQL, corpus and IBM regressions, image build |
