# Rules: Schema, Extraction and Normalization

This document defines how COBOL decision logic becomes a canonical, machine-readable rule.

## 1. Canonical rule schema (JSON)
```json
{
  "rule_id": "TXNCHK-R014",
  "version": 1,
  "title": "High-value transaction by minor",
  "intent": "Flag high-value transactions made by minors for review",
  "domain": "fraud_scoring",
  "conditions": {
    "all": [
      {"field": "customer_age", "source_name": "CUST-AGE", "op": "<", "value": 18},
      {"field": "transaction_amount", "source_name": "TXN-AMOUNT", "op": ">", "value": 5000}
    ]
  },
  "actions": [
    {"type": "set", "target": "review_flag", "source_name": "FLAG-REVIEW", "value": "Y"}
  ],
  "else_actions": [],
  "trace": {
    "program": "TXNCHK.cbl",
    "paragraph": "CHECK-TXN",
    "line_start": 42,
    "line_end": 45,
    "copybooks": ["CUSTCOPY.cpy"]
  },
  "concepts": ["age", "amount", "minor", "review"],
  "external_dependency": false,
  "confidence": {
    "score": 0.97,
    "components": {"structural": 1.0, "differential": 0.98, "consistency": 0.9, "naming": 0.8}
  },
  "status": "needs_review",
  "provenance": {
    "parser_version": "0.1.0",
    "model": "cobol-llm-v1",
    "prompt_version": "p3",
    "source_hash": "sha256:...",
    "extracted_at": "2026-10-05T00:00:00Z"
  }
}
```

### Field notes
| Field | Meaning |
|---|---|
| `conditions` | Boolean tree using `all` (AND), `any` (OR), `not`; leaves are comparisons |
| `field` | Business-friendly name from the data dictionary or LLM; `source_name` keeps the original COBOL name |
| `op` | One of `==, !=, <, <=, >, >=, in, not_in, between, is_numeric, is_alphabetic, is_positive, is_negative, is_zero` |
| `actions` / `else_actions` | What happens when the condition is true or false |
| `trace` | Exact program, paragraph, line range and copybook origin |
| `status` | `candidate`, `needs_review`, `approved`, `rejected`, `superseded` |

## 2. COBOL construct to rule mapping
| COBOL construct | Mapped to | Notes |
|---|---|---|
| `IF a > b ... END-IF` | One rule with `conditions` and `actions` | Core case |
| `IF ... ELSE ...` | `actions` plus `else_actions` | |
| Nested `IF` | Conditions combined with `all` along the path | One rule per leaf path, or a decision tree |
| `ELSE IF` chain | Ordered rule set; later rules add `not` of earlier ones | Preserves first-match semantics |
| `EVALUATE TRUE WHEN cond ...` | One rule per `WHEN`, ordered | `WHEN OTHER` becomes default rule |
| `EVALUATE x WHEN 1 WHEN 2 THRU 5` | `==` and `between` conditions on `x` | Multiple subjects (`ALSO`) become `all` |
| 88-level condition name (`IF IS-MINOR`) | Expanded to the underlying field comparison | Uses data dictionary `VALUE` clauses |
| Abbreviated combined condition (`IF A > 5 AND < 10`) | Expanded to explicit comparisons | Subject carried over |
| Class and sign conditions (`NUMERIC`, `POSITIVE`) | `is_numeric`, `is_positive` etc. | |
| `MOVE literal TO field` in branch | `set` action | Flags, codes, statuses |
| `COMPUTE`, `ADD`, `MULTIPLY` in branch | `compute` action with expression | e.g. fee or interest tier calculation |
| `PERFORM paragraph` in branch | `perform` action, with the paragraph's rules linked | Called paragraph analysed separately |
| `CALL 'PROG'` | `external_dependency: true`, action type `call` | Not guessed |
| `CONTINUE` / `NEXT SENTENCE` | No-op | |
| `GO TO` | Flagged for review | Control flow may split slices |
| Table lookups (`OCCURS`, `SEARCH`) | `lookup` condition or action | Extracted when table is initialised in source |
| `COPY ... REPLACING` | Resolved before parsing | Trace keeps copybook origin |
| `REDEFINES` | Alias recorded in dictionary | Warn when fields overlap in a condition |

## 3. Normalization rules
1. **Field names:** use a lowercase `snake_case` business name. Derive from the COBOL name and comments; keep `source_name` unchanged.
2. **Numeric scale:** apply PIC scale. `PIC 9(7)V99` is a number with two implied decimals; compare as decimal values, not integers.
3. **Operators:** map COBOL words (`GREATER THAN`, `NOT <`, `NOT =`) to canonical operators. `NOT <` becomes `>=`.
4. **Literals:** strip quotes and normalise case only if the program is case-insensitive in that comparison; keep padding semantics in mind (`'Y'` vs `'Y '`).
5. **Boolean structure:** flatten nested `AND`/`OR`; convert to a canonical order so equivalent rules compare equal.
6. **First-match semantics:** when branches are exclusive by order (`ELSE IF`, `EVALUATE`), make exclusivity explicit with `not` conditions or a priority number.
7. **Rule IDs:** `<PROGRAM>-R<nnn>` in source order; stable across re-runs for unchanged code.
8. **Concepts:** assign 1 to 5 tags (age, amount, geography, account type, ...) from field names and intent, used for search.
9. **Defaults:** variables with `VALUE` clauses are recorded as default state, not as rules.

## 4. Extraction rules for the LLM stage
- Input: one slice (source lines), data-dictionary entries for fields it uses, fixed instructions.
- Output: JSON matching the schema, no free text outside fields.
- The model may propose `intent`, `title`, `field` names and `concepts`. It must not change comparison operators or literal values; those are taken from the AST and compared to the model output.
- If the AST condition and the model condition disagree, the AST wins and confidence is reduced.
- Any construct not in the mapping table is passed through as `unsupported_construct` and routed to review.

## 5. Confidence scoring
Initial weights (to be tuned on the validation split):

| Component | Weight | How it is computed |
|---|---|---|
| Structural match | 0.40 | Model rule vs AST condition: exact equivalence = 1.0, partial = scaled, mismatch = 0 |
| Differential agreement | 0.30 | Share of generated inputs where rule output equals compiled COBOL output |
| Self-consistency | 0.20 | Agreement across several model runs on the same slice |
| Naming clarity | 0.10 | Field-name quality: dictionary-backed and comment-backed names score higher |

`score = 0.40*structural + 0.30*differential + 0.20*consistency + 0.10*naming`

Routing (initial thresholds): **0.90 or higher** and structural = 1.0 gives `candidate` (still reviewable); **0.70 to 0.90** gives `needs_review`; **below 0.70** or any `unsupported_construct` or `external_dependency` gives `needs_review` with a warning. Only a reviewer can set `approved`.

## 6. Worked examples

### 6.1 Simple IF
```cobol
IF CUST-AGE < 18 AND TXN-AMOUNT > 5000
    MOVE 'Y' TO FLAG-REVIEW
END-IF.
```
Rule: `all(customer_age < 18, transaction_amount > 5000)` then `set review_flag = 'Y'`.

### 6.2 EVALUATE to decision tree
```cobol
EVALUATE TRUE
   WHEN ACCT-TYPE = 'S' AND BALANCE >= 10000
        MOVE 0.045 TO INT-RATE
   WHEN ACCT-TYPE = 'S'
        MOVE 0.030 TO INT-RATE
   WHEN OTHER
        MOVE 0.010 TO INT-RATE
END-EVALUATE.
```
Rules in priority order: R1 (savings and balance >= 10000 gives 0.045), R2 (savings gives 0.030, with exclusivity from R1 recorded), R3 (default gives 0.010). Exported as a decision tree on `account_type` then `balance`.

## 7. Export formats
- **Rules JSON:** list of rules in the canonical schema.
- **Decision-tree JSON:** nodes with `test`, `true_branch`, `false_branch`, `leaf` actions; built when rules share a common root decision.
- **PMML:** `TreeModel` generated from decision-tree JSON for tree-shaped rule sets with a single output field. Other rule sets stay JSON-only.

## 8. Rule lifecycle
`candidate` or `needs_review` then `approved` or `rejected`. An approved rule becomes `superseded` when re-extraction from changed source produces a new version; the old version is kept for audit.
