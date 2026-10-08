# Error analysis

Generated from results/eval_*.json. Examples are verbatim model output.

## Full pipeline: why rules were routed to review

24 of 151 test rules went to review. Warnings raised:

- intent mentions a number that is not in the code: 11
- confidence below 0.90 - weakest consistency: 8
- confidence below 0.90 - weakest naming: 6

## Full pipeline: field-name mistakes (first 10)

| Rule | COBOL name | Model said | Gold |
|---|---|---|---|
| INT0003-R001 | WS-TRM | deposit_term | term_months |
| INT0003-R002 | WS-TRM | deposit_term | term_months |
| INT0003-R003 | WS-TRM | deposit_term_months | term_months |
| INT0003-R004 | WS-TRM | deposit_term | term_months |
| CRD0003-R001 | CREDIT-LIMIT | max_loan | credit_limit |
| CRD0003-R002 | CREDIT-LIMIT | max_loan | credit_limit |
| CRD0003-R003 | CREDIT-LIMIT | max_loan | credit_limit |
| PEN0003-R001 | L-PY | days_since_last_payment | late_count_12m |
| PEN0003-R001 | AS-CD | penalty_code | account_status |
| PEN0003-R002 | AS-CD | penalty_code | account_status |

Exact match is strict. Some of these are reasonable synonyms (for example `deposit_term` for `term_months`). Others are names the model copied from training templates: 8 of the 25 recorded mistakes use a business name that belongs to a different field in the training split (for example `CREDIT-LIMIT` named `max_loan`). That is the overfitting described in docs/limitations.md.

## Full pipeline: intents citing numbers not in the code (11 recorded)

- PEN0003-R002: "Charge 1 when a payment is more than 90 days late."
- INT0006-R003: "Pay 4.5% interest on loans made between 20 and 35 years."
- PEN0006-R002: "Charge 1 when a payment is more than 90 days late."
- PEN0009-R002: "Charge a penalty of at least 1 when a payment is more than 60 days late."
- PEN0012-R002: "Mark accounts that are 91 days or more late as delinquent."
- PEN0021-R002: "Charge 1 when an employee is older than 60."
- PEN0027-R002: "Charge a penalty of at least 1 when a payment is more than 60 days late."
- PEN0030-R002: "Charge a penalty of at least 1 when a payment is more than 60 days late."
- INT0045-R003: "Add a further 4.5% to the rate for deposits made at least 20 years old."
- PEN0045-R002: "Mark accounts that are 61 or more days late as bad."

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
