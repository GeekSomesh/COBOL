# Measured results

Test split: held-out templates, generated 2026-10-08.

| System | Precision | Recall | F1 | F1, logic only | Trace acc. | Behavioural agreement | Field names | Intent similarity | s/program |
|---|---|---|---|---|---|---|---|---|---|
| AST-only (no LLM) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.192 | n/a | <0.01 |
| LLM-only, zero-shot on raw code (7B), first 20 test programs | 0.000 | 0.000 | 0.000 | 0.183 | 0.000 | 0.621 | 0.261 | 0.775 | 25.24 |
| LLM on slices, prompted 1.5B Q8 (no fine-tune) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.566 | 0.835 | 3.28 |
| LLM on slices, prompted 7B (no fine-tune) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.632 | 0.856 | 10.95 |
| Full pipeline (fine-tuned 1.5B + verification) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.542 | 0.820 | 10.82 |

## Enrichment model selection (validation split, held-out templates)

Criterion: mean of field_name_exact, intent_similarity, concept_jaccard. The test split was not used for selection.

| Model | Field names (exact) | Intent similarity | Concept overlap | Intent numbers grounded | s/rule | Selection score |
|---|---|---|---|---|---|---|
| fine-tuned A (2 epochs, lr 2e-4) - selected | 0.607 | 0.795 | 0.474 | 0.955 | 1.23 | 0.626 |
| fine-tuned B (0.6 epoch, lr 1e-4) | 0.507 | 0.802 | 0.397 | 0.946 | 1.10 | 0.569 |
| base 1.5B Q8_0, 3-shot | 0.476 | 0.786 | 0.327 | 0.982 | 1.23 | 0.529 |
| base 1.5B Q4_K_M (Ollama), 3-shot | 0.444 | 0.784 | 0.330 | 0.991 | 2.09 | 0.519 |
| base 7B Q4_K_M (Ollama), 3-shot | 0.522 | 0.822 | 0.453 | 1.000 | 4.71 | 0.599 |

## Verification ablation (fault injection)

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

## Latency

- GET /v1/rules?q: median 24.14 ms, p95 47.51 ms
- GET /v1/rules/{id}: median 3.29 ms, p95 4.84 ms
- GET /v1/rules/{id}/trace: median 3.2 ms, p95 4.46 ms
- scoring (in process): median 0.052 ms, p95 0.117 ms
