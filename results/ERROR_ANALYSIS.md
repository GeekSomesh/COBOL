# Error analysis

Generated from results/eval_*.json. Examples are verbatim model output.

## Full pipeline: why rules were routed to review

10 of 151 test rules went to review. Warnings raised:

- intent mentions a number that is not in the code: 4
- confidence below 0.90 - weakest naming: 2
- confidence below 0.90 - weakest consistency: 2
- intent says '> 19' but the rule tests WS-TRM >= 19: 1
- intent says '>= 1' but the rule tests TM-MM < 1: 1
- intent says '<= 12' but the rule tests TM-MM > 12: 1

## Full pipeline: field-name mistakes (first 10)

| Rule | COBOL name | Model said | Gold |
|---|---|---|---|
| INT0003-R001 | WS-TRM | deposit_term_months | term_months |
| INT0003-R002 | WS-TRM | deposit_term | term_months |
| INT0003-R003 | WS-TRM | deposit_term_months | term_months |
| CRD0003-R001 | CREDIT-LIMIT | max_loan | credit_limit |
| CRD0003-R002 | CREDIT-LIMIT | max_limit | credit_limit |
| CRD0003-R003 | CREDIT-LIMIT | max_limit | credit_limit |
| PEN0003-R001 | L-PY | days_late | late_count_12m |
| PEN0003-R001 | WS-DLT | days_since_due | days_late |
| PEN0003-R001 | AS-CD | customer_status | account_status |
| PEN0003-R002 | AS-CD | penalty_code | account_status |

Exact match is strict. Some of these are reasonable synonyms (for example `deposit_term` for `term_months`). Others are names the model copied from training templates: 7 of the 25 recorded mistakes use a business name that belongs to a different field in the training split (for example `CREDIT-LIMIT` named `max_loan`). That is the overfitting described in docs/limitations.md.

## Full pipeline: intents citing numbers not in the code (4 recorded)

- INT0012-R002: "Pay 3.5% interest on deposits of at least 4 months but less than 19 months."
- PEN0018-R002: "Refer accounts that are 91 days or more overdue to special investigations."
- CRD0039-R001: "Lend up to 30% of annual income to applicants with a credit score of 740 or more and at least 1 year of employment."
- INT0045-R003: "Pay 4.5% interest on deposits of at least 20 months."

## LLM-only (no parser): failure categories

61 gold rules, 59 predicted, 0 matched.

- condition mismatch: 25
- action mismatch: 13
- trace mismatch: 11
- condition and action mismatch: 10
- missing rule: 2

Examples (gold vs nearest prediction, canonical form):

- INT0003-R001 (condition mismatch)
  - gold: `(('all', ('WS-TRM <= n:3', 'WS-TRM >= n:1')), (('set', 'WS-IRT', 'n:0.0275'),), ())`
  - pred: `('TRM-BAND-1-3 == n:1', (('set', 'WS-IRT', 'n:0.0275'),), ())`
- INT0003-R002 (condition mismatch)
  - gold: `(('all', ('WS-TRM <= n:18', 'WS-TRM >= n:4', ('any', ('WS-TRM < n:1', 'WS-TRM > n:3')))), (('set', 'WS-IRT', 'n:0.035'),), ())`
  - pred: `('TRM-BAND-4-18 == n:1', (('set', 'WS-IRT', 'n:0.035'),), ())`
- INT0003-R003 (condition mismatch)
  - gold: `(('all', ('WS-TRM <= n:36', 'WS-TRM >= n:19', ('any', ('WS-TRM < n:1', 'WS-TRM > n:3')), ('any', ('WS-TRM < n:4', 'WS-TRM > n:18')))), (('set', 'WS-IRT', 'n:0.0425'),), ())`
  - pred: `('TRM-BAND-19-36 == n:1', (('set', 'WS-IRT', 'n:0.0425'),), ())`

## Faults the behavioural test could not see

21 of 151 injected faults change no output for any input tried (for example, a range bound raised beyond the field's PIC maximum, or a threshold moved inside a region an earlier branch already claims). No behavioural test can detect those; the symbolic check still flags every one because the rule no longer matches the parsed source. On the behaviour-changing faults, the differential test alone caught 1.000.
