# Security Model

## 1. Principle
**Source in, decision model out.** The pipeline reads COBOL source code, an artifact already resident on the mainframe, and produces rules. It does not ingest, scan, store or expose customer transaction data or PII, and it makes no outbound network calls (zero data egress).

> Controls below are design targets for the prototype and the production path. Items marked *(planned)* are not part of the demo build.

## 2. Assets to protect
| Asset | Why it matters |
|---|---|
| COBOL source and copybooks | Proprietary intellectual property; reveals business logic |
| Extracted rule set | Describes fraud and credit logic; attackers could use it to evade controls |
| Model weights and prompts | Integrity of extraction depends on them |
| Review decisions and audit log | Evidence for auditors |

## 3. Threat model
| # | Threat | Impact | Mitigation |
|---|---|---|---|
| T1 | Source code leaks to an external LLM or service | IP and logic exposure | Local inference only; no outbound network; egress blocked at container level |
| T2 | Prompt injection through COBOL comments or string literals (e.g. a comment saying "ignore previous instructions") | Corrupted rules | Treat source as data; comments passed in a clearly delimited block or stripped by default; schema-constrained JSON output; no tool use by the model; verification step catches mismatches |
| T3 | LLM hallucinates or misreads a rule | Wrong rule published | Symbolic check against AST, differential testing, confidence score, human approval before `approved` status |
| T4 | Unauthorized access to rules or source | Logic exposure | Authentication on API and UI, role-based access, least privilege |
| T5 | Tampering with stored rules | Audit integrity lost | Versioned rule store, content hash per rule version, append-only audit log |
| T6 | Compromised model weights or dependencies | Backdoored extraction | Pin versions, verify checksums of weights and packages, internal mirror *(planned)* |
| T7 | Sensitive data in logs | Data exposure | Log metadata (IDs, line ranges, scores), not full source; configurable redaction |
| T8 | Scoring endpoint receives real customer values | PII handling risk | Scoring is stateless; inputs are not persisted or logged; endpoint can be disabled or deployed separately |
| T9 | Denial of service through huge or malformed files | Availability | File size limits, parser timeouts, job queue with quotas |

## 4. Controls

### 4.1 Data handling
- Inputs: COBOL source, copybooks, optional JCL for context. No transaction data.
- Datasets used for the datathon are synthetic or public ([dataset.md](dataset.md)). No customer or PII data.
- Uploaded source is kept only as long as needed for extraction and review, with a configurable retention period.

### 4.2 Access control
- Roles: `viewer` (read rules), `reviewer` (approve/reject/edit), `admin` (manage sources and users).
- Integration with the platform's enterprise identity and security manager (for example RACF or SSO) *(planned)*.
- All API calls require a token; the UI uses the same API.

### 4.3 Network and deployment
- No internet access for the inference and analysis containers.
- TLS for API and UI traffic inside the network.
- Secrets are supplied through environment or a secret manager, not stored in code.

### 4.4 Integrity and auditability
- Each rule version stores: content hash, source file hash, parser version, model version, prompt version, timestamp.
- Every review action (approve, reject, edit) is recorded with user and reason.
- Re-running extraction on the same source and versions should reproduce the same rule set (temperature 0 or fixed seed), or flag differences.

### 4.5 LLM-specific controls
- Output must validate against the rule JSON schema; invalid output is retried, then sent to review.
- The model never executes code or calls external tools.
- Prompts contain only the slice, its data-dictionary entries and fixed instructions.

## 5. Privacy and compliance alignment
- **Data minimization:** only non-sensitive source artifacts are processed.
- **Data residency:** processing stays in the institution's environment.
- **Explainability and audit:** every rule has intent, confidence, and line-level traceability, which supports audit and regulator questions.

## 6. Residual risks
- A fine-tuned model can still misread unusual code; this is why human approval gates the `approved` state.
- Business logic exposed in the rule catalogue is itself sensitive; restrict access accordingly.
