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
