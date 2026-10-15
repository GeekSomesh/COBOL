# Measured results

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

## Enrichment model selection (validation split, held-out templates)

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

## Verification ablation (fault injection)

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

## Confidence calibration

Isotonic map from score to P(rule correct), fitted on 224 validation fault cases and measured on 302 test cases.

| | Raw score | Calibrated (`p_correct`) |
|---|---|---|
| Expected calibration error | 0.303 | 0.032 |
| Brier score | 0.237 | 0.061 |

## Real code: IBM Z Open Editor samples

- Parser vs hand-drafted expected rules (unverified by a COBOL expert): 45 of 45 rules match, F1 1.000 (first run: SAM1 0.885, SAM2 0.947; one parser fix, one draft fix).
- SAM2, a CALLed subprogram, run through its extracted rules vs the compiled program: agreement 1.000 on 427 inputs, comparing every parameter record byte for byte; 35 inputs excluded because COBOL leaves them undefined (non-digit bytes compared as a number).
- Control: with the sign flip removed from SAM2-R011, agreement drops to 0.993 (caught: True).

## Performance on large programs

| Lines | Paragraphs | Rules | Analyse s | Rules s | DMN s | Rules/s |
|---|---|---|---|---|---|---|
| 1,019 | 111 | 233 | 0.04 | 0.05 | 0.01 | 2,721 |
| 5,141 | 666 | 1,406 | 0.22 | 0.30 | 0.07 | 2,710 |
| 20,623 | 2,750 | 5,811 | 0.92 | 1.62 | 0.32 | 2,285 |
| 51,571 | 6,916 | 14,621 | 2.54 | 3.94 | 0.83 | 2,255 |

## Latency

- GET /v1/rules?q: median 23.98 ms, p95 46.67 ms
- GET /v1/rules/{id}: median 3.69 ms, p95 4.44 ms
- GET /v1/rules/{id}/trace: median 3.51 ms, p95 4.41 ms
- scoring (in process): median 0.083 ms, p95 0.21 ms
