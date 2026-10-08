# Requirements

## 1. Purpose
Define what the COBOL-to-Decision Pipeline must do. Priorities use MoSCoW: **Must** (needed for the demo), **Should** (strong value), **Could** (stretch).

## 2. Personas and user stories
| Persona | Story |
|---|---|
| Bank risk and compliance officer | As an officer, I want to see all rules behind a decision with source-line references, so I can prove why it was made. |
| Fraud and AML team member | As an analyst, I want to find fraud patterns already encoded in legacy COBOL, so I can reuse them in new detection models. |
| Mainframe / application developer | As a developer, I want to document unfamiliar programs without manual reverse engineering. |
| Product manager / business analyst | As an analyst, I want to ask "show all rules affecting customers under 18" and get an answer without reading COBOL. |
| Data scientist / AI engineer | As a data scientist, I want structured rules and features exported from mainframe logic, so I can train new models. |
| IT architect | As an architect, I want an automated inventory of embedded business rules to plan migration. |

## 3. Functional requirements

### 3.1 Ingestion and parsing
| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-01 | Accept COBOL programs and copybooks (upload or directory) | Must | Files load; origin recorded |
| FR-02 | Resolve COPY statements, including REPLACING | Must | Resolved source maps back to original file and line |
| FR-03 | Parse DATA DIVISION and PROCEDURE DIVISION into an AST | Must | Parses all programs in the synthetic corpus without error |
| FR-04 | Build data dictionary (PIC, USAGE, REDEFINES, OCCURS, 88-levels) | Must | Every referenced field resolves to a typed entry |
| FR-05 | Build control-flow graph for PERFORM and paragraph flow | Should | Paragraph call graph produced |

### 3.2 Rule extraction
| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-06 | Identify IF, ELSE-IF and EVALUATE decision points | Must | All decision points in test programs found |
| FR-07 | Slice statements controlled by each decision | Must | Slice includes actions (MOVE, COMPUTE, CALL, PERFORM) |
| FR-08 | Generate plain-English intent and structured rule per slice | Must | Output validates against rule schema ([rules.md](rules.md)) |
| FR-09 | Handle 88-level condition names and abbreviated conditions | Should | Resolved to explicit field comparisons |
| FR-10 | Mark rules with external dependencies (CALL, CICS, DB2) | Should | Flagged `external_dependency` |

### 3.3 Verification and trust
| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-11 | Symbolic check of each rule against its AST condition | Must | Mismatches flagged |
| FR-12 | Differential testing against compiled COBOL on generated inputs | Should | Agreement rate reported per rule |
| FR-13 | Confidence score per rule | Must | Score in [0,1] with component breakdown |
| FR-14 | Review workflow (approve, reject, edit) for low-confidence rules | Must | Status transitions recorded with user and reason |
| FR-15 | Source traceability: program, copybook, line range | Must | 100% of rules carry a valid trace |

### 3.4 Access and output
| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-16 | Business-concept search ("age", "amount", "geography") | Must | Query returns relevant rules with source links |
| FR-17 | REST API for search, retrieval, review, export ([api.md](api.md)) | Must | OpenAPI spec served; endpoints functional |
| FR-18 | Export rules as JSON and decision-tree JSON | Must | Valid, schema-conformant files |
| FR-19 | Export tree-shaped rule sets as PMML | Should | PMML validates in a standard reader |
| FR-20 | Interactive decision explorer with side-by-side COBOL and rule view | Must | Click a rule, see highlighted source lines |
| FR-21 | SHAP-based feature influence view | Should | Chart per rule set from surrogate model |
| FR-22 | Detect duplicate, overlapping and conflicting rules | Could | Report lists pairs with reason |
| FR-23 | Stateless real-time scoring endpoint using approved rules | Could | Returns decision and fired rules, stores nothing |

## 4. Non-functional requirements
| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Security | Source-in, decision-model-out; zero data egress; see [security.md](security.md) |
| NFR-02 | Privacy | No customer, transaction or PII data in inputs or datasets |
| NFR-03 | Accuracy | Targets (to be measured): rule F1 of 0.90 or higher on the gold standard; behavioural agreement of 0.95 or higher |
| NFR-04 | Traceability | Every rule links to exact source lines |
| NFR-05 | Performance | A typical program of about 1,000 lines processed in minutes; API read calls under 200 ms; scoring under 50 ms |
| NFR-06 | Reproducibility | Same input, parser, model and prompt versions give the same output |
| NFR-07 | Portability | Containerised; runs on IBM Z Open Platform / LinuxONE and standard Linux |
| NFR-08 | Usability | Business users can search and read rules without COBOL knowledge |
| NFR-09 | Auditability | All review actions logged |

## 5. Constraints and assumptions
- COBOL dialect: start with a defined subset of Enterprise COBOL syntax; extend iteratively.
- Training and evaluation data are synthetic or public ([dataset.md](dataset.md)).
- The datathon build targets a working prototype, not full production hardening.
- Model runs locally; model size is chosen to fit available hardware.

## 6. Out of scope (this phase)
- Automatic COBOL-to-Java/Python migration.
- Replacing or modifying the production COBOL system.
- Autonomous deployment of rules without human approval.
- Complete coverage of every COBOL dialect and embedded CICS/SQL construct.

## 7. MVP definition
Must items above, demonstrated end to end on at least one banking-style program from the synthetic corpus plus one public sample: upload, extract, verify, review, search, trace to source, export.
