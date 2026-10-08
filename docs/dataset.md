# Datasets

All datasets are synthetic or public. **No customer, transaction or PII data is used.** Synthetic generation uses Python and Faker with domain templates, and the generated corpus is included in the submission package.

## 1. Synthetic COBOL rule corpus (primary)
Realistic COBOL programs across 12 business domains:

| # | Domain | Example rule |
|---|---|---|
| 1 | Credit approval | Score below cutoff then refer to underwriter |
| 2 | Insurance underwriting | Age and risk class then premium band |
| 3 | Fraud scoring | Minor and amount over limit then flag |
| 4 | KYC thresholds | Cash deposit over limit then enhanced checks |
| 5 | Interest rate tiers | Balance tier then rate |
| 6 | Fee exemptions | Account type and balance then waive fee |
| 7 | Loan limits | Income multiple then maximum loan |
| 8 | Account eligibility | Age and residency then eligible product |
| 9 | Discount rules | Customer tier then discount percent |
| 10 | Currency conversion | Currency code then rate and rounding |
| 11 | Tax calculation | Bracket then tax amount |
| 12 | Penalty assessment | Days late then penalty |

Each program includes: copybook definitions, DATA DIVISION declarations with PIC clauses, and PROCEDURE DIVISION logic with nested IF, EVALUATE and MOVE chains.

### Realistic imperfections (to test robustness)
- Multi-file programs and copybook includes (`COPY ... REPLACING`)
- JCL references and legacy comment styles
- Cryptic data names and abbreviated conditions
- 88-level condition names and REDEFINES
- Dead code and commented-out rules
- A small share of programs with `GO TO` and external `CALL`s

### Generator design
1. Define a rule spec per domain (fields, thresholds, outcomes) as structured data.
2. Render the spec into COBOL through templates with randomised names, layouts and comment styles.
3. Store the spec as the **ground-truth rule** next to the generated program, so labels come for free.
4. Randomise structure: nested IF vs EVALUATE, ELSE chains, abbreviated conditions.
5. Compile with GnuCOBOL to confirm each program is valid and to enable differential testing.

## 2. Public COBOL examples
- Open-source COBOL repositories on GitHub (check each licence; keep only permissive or open licences).
- IBM Z Open Editor sample COBOL programs and copybooks (public IBM sample repository; check the licence before use).
- The GnuCOBOL test suite (IF, EVALUATE and COPY patterns).

Use: robustness testing and qualitative demo on code not written by us. Not used as labelled ground truth unless rules are documented by hand.

## 3. Decision-model gold standard
Manually documented decision logic, 50 to 100 rules per domain, stored as JSON, decision trees and PMML. Synthetic programs get their gold rules from the generator; public programs get a smaller, hand-labelled set. Used to measure precision, recall, F1 and semantic similarity ([evaluation.md](evaluation.md)).

## 4. Explainability and traceability records
Mapping dataset pairing each extracted rule with: source line numbers, program or function name, paragraph, and copybook origin. Used to verify traceability and audit readiness.

## 5. Splits
| Split | Use | Share |
|---|---|---|
| Train | Fine-tuning the LLM | 70% of synthetic programs |
| Validation | Prompt tuning, confidence weights | 15% |
| Test | Final reported metrics | 15%, plus public programs |

Split by program template and domain variant so near-duplicates do not leak across splits.

## 6. Record formats
**Training example (JSONL):**
```json
{"slice": "IF CUST-AGE < 18 AND TXN-AMOUNT > 5000 ...",
 "dictionary": {"CUST-AGE": "PIC 9(3)", "TXN-AMOUNT": "PIC 9(7)V99"},
 "target_rule": { "...": "canonical rule JSON, see rules.md" }}
```
**Traceability record:**
```json
{"rule_id": "TXNCHK-R014", "program": "TXNCHK.cbl", "paragraph": "CHECK-TXN",
 "line_start": 42, "line_end": 45, "copybooks": ["CUSTCOPY.cpy"]}
```

## 7. Data quality checks
- Every generated program compiles under GnuCOBOL.
- Every gold rule maps to exactly one source line range.
- No duplicate programs across splits.
- No real names, account numbers or personal data (Faker output only).
