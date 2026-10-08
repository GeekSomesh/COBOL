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
