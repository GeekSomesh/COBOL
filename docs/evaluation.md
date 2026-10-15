# Evaluation Plan

All numbers below are **targets to be measured**, not results.

## 1. What we measure
| Metric | Definition | Target |
|---|---|---|
| Rule precision | Share of extracted rules that match a gold rule | 0.90 or higher |
| Rule recall | Share of gold rules that were extracted | 0.90 or higher |
| Rule F1 | Harmonic mean of precision and recall | 0.90 or higher |
| Behavioural agreement | Share of generated test inputs where extracted rules and compiled COBOL give the same outcome | 0.95 or higher |
| Intent similarity | Embedding similarity between generated intent and gold intent | Report; track trend |
| Traceability accuracy | Rules whose trace covers the correct line range | 1.00 |
| Confidence calibration | Expected calibration error of the confidence score vs correctness | Report |
| Review effort | Time to document one program, manual vs tool-assisted | Report ratio |
| Latency | Time per program, API read latency, scoring latency | Minutes per ~1,000 lines; under 200 ms reads; under 50 ms scoring |

## 2. Defining a rule match
A predicted rule matches a gold rule when:
1. **Condition equivalence:** after normalization ([rules.md](rules.md)), the two boolean conditions are logically equivalent. Check by canonical form comparison, with truth-table or SMT checking over bounded field domains for complex cases.
2. **Action equivalence:** same target fields and values.
3. **Trace overlap:** line ranges overlap by at least 80%.

Partial matches (right condition, wrong action, etc.) count as a failure but are tallied separately for error analysis.

## 3. Differential testing
1. Compile each synthetic program with GnuCOBOL.
2. Generate inputs covering each branch plus boundary values (for example 17, 18 and 19 for an age test) and random values.
3. Run the COBOL program and the extracted rule set on the same inputs.
4. Compare output fields. Report agreement per rule and per program.

As built: the extracted rules are executed by an interpreter that follows the
program's control flow (PERFORM, GO TO, loops) and runs every decision only through
its rules; outputs are compared with the compiled program's. Called subprograms run
through a generated driver and whole parameter records are compared byte for byte.
Inputs come from boundary values, targeted searches for every condition and
sub-condition, random rows and (for subprograms) a coverage-guided search for rule
combinations. Rows that make COBOL compare invalid numeric data are excluded and counted.

## 4. Baselines and ablations
| System | Purpose |
|---|---|
| Regex or AST-only extraction (no LLM) | Shows what structure alone gives: conditions and actions, no intent |
| LLM-only zero-shot on raw code | Shows hallucination and trace problems without parsing |
| LLM on slices, no fine-tuning | Isolates the value of fine-tuning |
| **Full pipeline** | Parser, slicing, fine-tuned LLM, verification |
| Full pipeline without verification | Isolates the value of verification and confidence routing |

## 5. Experiment protocol
1. Freeze train, validation and test splits ([dataset.md](dataset.md)).
2. Tune prompts and confidence weights on validation only.
3. Run each system on the test split; report metrics per domain and overall.
4. Evaluate on public COBOL programs qualitatively and, where hand-labelled, quantitatively.
5. Error analysis: categorise failures (unsupported construct, 88-level mis-expansion, numeric scale, first-match semantics, hallucinated field).

### Additional studies (as built)
| Study | Command | Output |
|---|---|---|
| Fault injection (validation and test) | `scripts/evaluate.py faults [--split val]` | `results/eval_faults*.json` |
| Confidence calibration (fit on validation faults, measure on test) | `scripts/evaluate.py calibrate` | `results/eval_calibration.json`, `data/calibration.json` |
| Real code: IBM samples vs drafted expected rules, SAM2 vs compiled subprogram | `scripts/evaluate.py real` | `results/eval_real.json`, `results/public_gold.json` |
| Intent wording check: false alarms on gold intents, detection of flipped wordings | see `tests/test_llm.py` and STATUS.md | 0 false alarms on 965, 97% caught |
| Performance on generated programs up to 51k lines | `scripts/perf.py` | `results/perf.json` |
| Fine-tune round 2 (21 templates), selection on validation, single test run | `scripts/enrich_eval.py`, `scripts/evaluate.py system llm_slices` | `results/enrich_ft_*_val.json`, `results/eval_llm_slices_*_p4.json` |

All LLM systems in the current table use the same prompt (version p4); the first
round's numbers (prompt p2) are kept in the model-selection table for reference.

## 6. Reporting template
| System | Precision | Recall | F1 | Behavioural agreement | Intent similarity |
|---|---|---|---|---|---|
| AST-only | | | | | |
| LLM-only | | | | | |
| LLM on slices | | | | | |
| Full pipeline | | | | | |
| Full, no verification | | | | | |

## 7. Human evaluation
Ask 3 to 5 reviewers (for example classmates or mentors with COBOL or domain knowledge) to rate 30 rules for correctness and clarity on a 1 to 5 scale, and record time to understand a program with and without the tool.

## 8. Measured results
<!-- RESULTS:START -->
Test split: 50 programs from 3 held-out templates (never seen in training), generated 2026-10-15. All LLM rows use the same prompt (version p4).

| System | Precision | Recall | F1 | F1, logic only | Trace acc. | Behavioural agreement | Field names | Intent similarity | s/program |
|---|---|---|---|---|---|---|---|---|---|
| AST-only (no LLM) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.192 | n/a | <0.01 |
| LLM-only, zero-shot on raw code (7B), first 20 test programs | 0.000 | 0.000 | 0.000 | 0.183 | 0.000 | 0.621 | 0.261 | 0.775 | 25.24 |
| LLM on slices, prompted 1.5B Q8, 3-shot | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.563 | 0.837 | 3.52 |
| LLM on slices, prompted 7B, 3-shot | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.632 | 0.856 | 11.38 |
| LLM on slices, fine-tuned 1.5B run A (12 templates) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.542 | 0.818 | 3.18 |
| LLM on slices, fine-tuned 1.5B v2 (21 templates) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.590 | 0.839 | 3.05 |
| Full pipeline (fine-tuned 1.5B v2 + verification) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.596 | 0.839 | 10.99 |

#### Enrichment model selection (validation split, held-out templates)

Criterion: mean of field_name_exact, intent_similarity, concept_jaccard. The test split was not used for selection. Rows marked p2 were measured in the first round with an earlier prompt.

| Model | Prompt | Field names (exact) | Intent similarity | Concept overlap | Intent numbers grounded | s/rule | Selection score |
|---|---|---|---|---|---|---|---|
| fine-tuned v2, 2 epochs, 21 templates (selected) | p4 | 0.639 | 0.828 | 0.554 | 0.982 | 1.17 | 0.674 |
| fine-tuned v2e, 0.5 epoch (early stop) | p4 | 0.634 | 0.794 | 0.371 | 0.964 | 1.10 | 0.600 |
| fine-tuned A, 2 epochs, 12 templates | p4 | 0.607 | 0.795 | 0.474 | 0.955 | 1.19 | 0.626 |
| fine-tuned B (0.6 epoch, lr 1e-4) | p2 | 0.507 | 0.802 | 0.397 | 0.946 | 1.10 | 0.569 |
| base 1.5B Q8_0, 3-shot | p2 | 0.476 | 0.786 | 0.327 | 0.982 | 1.23 | 0.529 |
| base 1.5B Q4_K_M (Ollama), 3-shot | p2 | 0.444 | 0.784 | 0.330 | 0.991 | 2.09 | 0.519 |
| base 7B Q4_K_M (Ollama), 3-shot | p2 | 0.522 | 0.822 | 0.453 | 1.000 | 4.71 | 0.599 |

#### Verification ablation (fault injection)

151 rules with one injected fault (threshold, operator or action) and 151 clean rules.

| | With verification | Without verification |
|---|---|---|
| Behaviour-changing faults routed to review | 1.000 | 0.000 |
| All injected faults routed to review | 0.927 | 0.000 |
| Equivalent mutants (no input changes any output) / proven identical by the SMT check | 21 / 11 of 151 | - |
| Faults caught by differential test alone (all / behaviour-changing only) | 0.861 / 1.000 | - |
| Clean rules routed to review (excluding external/unsupported) | 0.000 | - |
| Mean confidence, clean vs faulty | 0.968 vs 0.637 | - |
| Confidence AUROC, clean vs faulty | 0.945 | - |

#### Confidence calibration

Isotonic map from score to P(rule correct), fitted on 224 validation fault cases and measured on 302 test cases.

| | Raw score | Calibrated (`p_correct`) |
|---|---|---|
| Expected calibration error | 0.303 | 0.032 |
| Brier score | 0.237 | 0.061 |

#### Real code: IBM Z Open Editor samples

- Parser vs hand-drafted expected rules (unverified by a COBOL expert): 45 of 45 rules match, F1 1.000 (first run: SAM1 0.885, SAM2 0.947; one parser fix, one draft fix).
- SAM2, a CALLed subprogram, run through its extracted rules vs the compiled program: agreement 1.000 on 427 inputs, comparing every parameter record byte for byte; 35 inputs excluded because COBOL leaves them undefined (non-digit bytes compared as a number).
- Control: with the sign flip removed from SAM2-R011, agreement drops to 0.993 (caught: True).

#### Performance on large programs

| Lines | Paragraphs | Rules | Analyse s | Rules s | DMN s | Rules/s |
|---|---|---|---|---|---|---|
| 1,019 | 111 | 233 | 0.04 | 0.05 | 0.01 | 2,721 |
| 5,141 | 666 | 1,406 | 0.22 | 0.30 | 0.07 | 2,710 |
| 20,623 | 2,750 | 5,811 | 0.92 | 1.62 | 0.32 | 2,285 |
| 51,571 | 6,916 | 14,621 | 2.54 | 3.94 | 0.83 | 2,255 |

#### Latency

- GET /v1/rules?q: median 23.98 ms, p95 46.67 ms
- GET /v1/rules/{id}: median 3.69 ms, p95 4.44 ms
- GET /v1/rules/{id}/trace: median 3.51 ms, p95 4.41 ms
- scoring (in process): median 0.083 ms, p95 0.21 ms
<!-- RESULTS:END -->
