# Roadmap

Phases are ordered by dependency; adjust durations to the datathon schedule.

| Phase | Goal | Key tasks | Done when |
|---|---|---|---|
| 0. Setup | Repo, environment, decisions | Repo layout, container base, pick COBOL dialect subset, choose base LLM | Repo builds; subset documented |
| 1. Foundation | Parse and analyse | ANTLR4 grammar, COPY resolver, data dictionary, CFG, decision slicer | All synthetic programs parse; slices produced with line ranges |
| 2. Data | Corpus and labels | Spec-driven COBOL generator, GnuCOBOL compile check, gold rules, splits | 12 domains generated, compile cleanly, gold rules attached |
| 3. Intelligence | LLM extraction | Prompt templates, fine-tuning on slice-to-rule pairs, constrained JSON output, normalizer | Valid schema output on validation split; baseline metrics |
| 4. Trust | Verification | AST symbolic check, differential testing, confidence scoring, review states | Confidence computed for all rules; mismatches flagged |
| 5. Access | API and explorer | REST API, concept search, source side-by-side view, review UI, JSON and PMML export | End-to-end flow works in UI |
| 6. Explainability | SHAP and conflicts | Surrogate model, SHAP view, duplicate and conflict detection | SHAP chart per rule set |
| 7. Demo | Polish and present | Evaluation run, results slide, demo data, rehearsal | Demo script ([demo.md](demo.md)) runs without edits |

## Priorities if time is short
1. Phases 0 to 1 and a minimal Phase 3 on a handful of domains.
2. Verification (Phase 4), because trust is the main differentiator.
3. Explorer with source highlighting (Phase 5).
4. Everything else is stretch.

## Risks
| Risk | Likelihood | Mitigation |
|---|---|---|
| ANTLR COBOL grammar gaps | Medium | Start from an existing open COBOL grammar; restrict to a documented subset |
| Fine-tuning compute is limited | Medium | Small model, LoRA-style adaptation, or few-shot prompting with retrieval as fallback |
| LLM errors on numeric or 88-level logic | Medium | AST wins on operators and literals; dictionary-based expansion before the LLM |
| Limited public COBOL data | High | Synthetic generator with built-in ground truth |
| Time | High | Priority order above; keep demo to one strong end-to-end path |

## Beyond the datathon (status 2026-10-16)
Done: control flow across paragraphs, lookup tables, behavioural testing of called
subprograms, SMT equivalence and conflict checks, impact analysis and JCL/call-graph
analysis, rule drift with witnesses, DMN export, review workflow (assignment,
comments, N approvers, four-eyes), OIDC and per-program access, PostgreSQL, hash-chained
audit log, backups, metrics, CI, container and Docker toolchain backends.

Still open:
- CICS and DB2 semantics (EXEC statements are kept and flagged, not interpreted).
- Behavioural testing of file-processing main programs (generate input files, compare output files).
- GO TO DEPENDING ON, ALTER, nested OCCURS, reference modification.
- Review of the drafted expected rules for the IBM samples by a COBOL expert; more real-code benchmarks.
- A larger and more varied training set for the fine-tuned model, then a fresh comparison with prompted models.
