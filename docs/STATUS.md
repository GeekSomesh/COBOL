# Requirements status (as built)

Each requirement from [requirement.md](requirement.md), where it lives, and what checks it.
Status: **Built** works in the demo and is tested. **Partial** works with stated limits. **Not built** is planned.

## Functional
| ID | Requirement | Status | Where | Checked by |
|---|---|---|---|---|
| FR-01 | Accept programs and copybooks | Built | `POST /v1/programs`, `src/ingest/source.py` | `tests/test_api.py` |
| FR-02 | Resolve COPY incl. REPLACING, map lines | Built | `src/ingest/source.py` | `tests/test_ingest.py` |
| FR-03 | Parse DATA and PROCEDURE DIVISION | Built (documented subset) | `src/analysis/parser.py` | all 300 corpus programs + IBM SAM1/SAM2 parse with no diagnostics |
| FR-04 | Data dictionary (PIC, USAGE, REDEFINES, OCCURS, 88) | Built | `src/analysis/dictionary.py` | `tests/test_dictionary.py` |
| FR-05 | Control-flow graph | Not built | - | listed in [limitations.md](limitations.md) |
| FR-06 | Find IF / ELSE-IF / EVALUATE decisions | Built | `src/analysis/slicer.py` | `tests/test_analysis.py` |
| FR-07 | Slice controlled statements | Built | `src/analysis/slicer.py` | `tests/test_analysis.py` |
| FR-08 | Intent + structured rule per slice | Built | `src/rules/baseline.py`, `src/llm/enrich.py` | schema-valid output on 100% of test rules |
| FR-09 | 88-levels and abbreviated conditions | Built | `src/analysis/parser.py` | `tests/test_analysis.py` |
| FR-10 | Flag external dependencies | Built | slicer (`CALL`, `EXEC`) | `tests/test_analysis.py` |
| FR-11 | Symbolic check against the AST | Built | `src/verify/checks.py: structural` | `tests/test_verify.py`, fault injection |
| FR-12 | Differential testing vs compiled COBOL | Built (ACCEPT/DISPLAY batch convention) | `src/verify/harness.py`, `checks.py: differential` | `tests/test_verify.py`, `results/eval_*.json` |
| FR-13 | Confidence with breakdown | Built | `src/verify/confidence.py` | `tests/test_verify.py` |
| FR-14 | Review workflow with audit | Built | `PATCH /v1/rules/{id}`, `src/api/store.py` | `tests/test_api.py` |
| FR-15 | Source traceability | Built | `Trace` on every rule | traceability accuracy 1.000 on the test split |
| FR-16 | Business-concept search | Built | `src/rules/search.py` | `tests/test_export_search.py` |
| FR-17 | REST API with OpenAPI | Built | `src/api/main.py`, `/openapi.json`, `/docs` | `tests/test_api.py` |
| FR-18 | JSON and decision-tree export | Built | `src/rules/export.py` | trees replay identically to rules (`tests/test_export_search.py`) |
| FR-19 | PMML export | Partial | `src/rules/export.py: to_pmml` | well-formed XML; single literal-output blocks only |
| FR-20 | Explorer with side-by-side source | Built | `src/ui/static/` | checked in a browser (search, highlight, review, export, score, SHAP, audit) |
| FR-21 | SHAP feature influence | Built | `src/rules/decide.py: explain` | `tests/test_export_search.py` |
| FR-22 | Duplicate / overlap / conflict detection | Built (sampled, not exhaustive) | `src/rules/decide.py: conflicts` | `tests/test_export_search.py` |
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
| NFR-07 | Containerised | Built (API image); compose untested | `docker build` succeeds; container smoke test: upload, extract, native GnuCOBOL differential agreement 1.0. The two-service compose file with Ollama was not run |
| NFR-08 | Usable without COBOL knowledge | Built | Business names, intents, concept search |
| NFR-09 | All review actions logged | Built | `reviews` table, `/v1/audit` |
