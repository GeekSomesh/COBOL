# REST API Specification (v1)

Base path: `/v1`. JSON requests and responses. Auth: `Authorization: Bearer <token>`. An OpenAPI document is served at `/openapi.json`.

## 1. Programs
| Method | Path | Description |
|---|---|---|
| POST | `/programs` | Upload a program and its copybooks (multipart) |
| GET | `/programs` | List programs |
| GET | `/programs/{id}` | Metadata, parse status, rule counts |
| GET | `/programs/{id}/source` | Resolved source with line mapping |
| POST | `/programs/{id}/extract` | Start extraction job (async) |
| GET | `/jobs/{job_id}` | Job status and progress |

**Upload response**
```json
{"program_id": "p_001", "name": "TXNCHK.cbl", "copybooks": ["CUSTCOPY.cpy"], "status": "uploaded"}
```

**Job status**
```json
{"job_id": "j_17", "program_id": "p_001", "state": "running", "slices_total": 42, "slices_done": 17}
```

## 2. Rules
| Method | Path | Description |
|---|---|---|
| GET | `/rules` | Search and filter rules |
| GET | `/rules/{rule_id}` | Full rule (schema in [rules.md](rules.md)) |
| GET | `/rules/{rule_id}/trace` | Source lines, paragraph, copybook origin |
| PATCH | `/rules/{rule_id}` | Review action: approve, reject or edit |
| GET | `/rules/{rule_id}/history` | Versions and review log |
| GET | `/conflicts` | Rule pairs that can fire together and set a field to different values (SMT-proven, with an example input) |
| POST | `/rules/bulk_review` | Approve or reject many rules with one reason; per-rule results |
| POST | `/rules/{rule_id}/assign` | Assign a rule to a reviewer (notifies them) |
| GET / POST | `/rules/{rule_id}/comments` | Read / add comments (notifies the people involved) |
| GET | `/review_queue?mine=` | Rules waiting for review, assignment and approvals so far |
| GET | `/notifications?unread=` | The signed-in user's notifications; `POST /notifications/{id}/read` |

**Query parameters for `GET /rules`**
| Param | Example | Meaning |
|---|---|---|
| `q` | `customers under 18` | Business-concept search |
| `concept` | `age` | Concept tag filter |
| `program` | `TXNCHK.cbl` | Program filter |
| `domain` | `fraud_scoring` | Domain filter |
| `status` | `needs_review` | Lifecycle filter |
| `min_confidence` | `0.8` | Minimum score |
| `page`, `page_size` | `1`, `25` | Pagination |

**Example**
```
GET /v1/rules?q=customers%20under%2018&min_confidence=0.8
```
```json
{"total": 1, "items": [{
  "rule_id": "TXNCHK-R014",
  "title": "High-value transaction by minor",
  "intent": "Flag high-value transactions made by minors for review",
  "confidence": {"score": 0.97},
  "status": "needs_review",
  "trace": {"program": "TXNCHK.cbl", "line_start": 42, "line_end": 45}
}]}
```

**Review action**
```
PATCH /v1/rules/TXNCHK-R014
{"action": "approve", "reason": "Matches policy document section 4"}
```
Allowed actions: `approve`, `reject`, `edit` (with a `changes` object). Requires `reviewer` role.

## 3. Export
| Method | Path | Description |
|---|---|---|
| GET | `/export?format=json` | Rules in canonical schema |
| GET | `/export?format=tree` | Decision-tree JSON |
| GET | `/export?format=pmml` | PMML 4.4 TreeModel for single-output decision blocks (422 when none qualifies) |
| GET | `/export?format=dmn` | DMN 1.3 decision tables, one per decision block (FIRST hit policy) |

PMML and DMN are validated against the official XSDs before they are returned; the
number of decision blocks that could not be expressed is in `X-Skipped-Rule-Sets`.

Filters `program`, `domain`, `status` apply (for example `status=approved`).

## 4. Scoring (optional, stateless)
| Method | Path | Description |
|---|---|---|
| POST | `/score` | Evaluate approved rules against supplied field values |

```json
POST /v1/score
{"rule_set": "TXNCHK", "inputs": {"customer_age": 17, "transaction_amount": 7500}}
```
```json
{"decision": {"review_flag": "Y"}, "fired_rules": ["TXNCHK-R014"], "latency_ms": 3}
```
The endpoint is stateless: inputs are neither stored nor logged ([security.md](security.md), T8). It can be disabled or deployed separately.

## 5. Explainability
| Method | Path | Description |
|---|---|---|
| GET | `/explain/{rule_set}` | Feature influence (SHAP values) from the surrogate model |

## 6. Repository, impact and drift
| Method | Path | Description |
|---|---|---|
| POST | `/jcl` | Upload a JCL member (admin): jobs, steps, programs, DD datasets |
| GET | `/jcl` | Uploaded JCL members |
| GET | `/repository` | Graph of programs, copybooks, JCL steps and datasets, with findings |
| GET | `/impact?kind=field\|copybook\|program\|dataset&name=` | Rules, programs, transitive callers and jobs affected by a change |
| GET | `/diff?old={program_id}&new={program_id}` | Rule drift between two uploads, with an input where a changed rule decides differently |
| GET / PUT | `/glossary` | Business glossary (PUT: admin) |

## 7. Administration and operations
| Method | Path | Description |
|---|---|---|
| POST | `/admin/backup?keep=` | Verified online backup (integrity + audit hash chain), rotation |
| GET | `/admin/backups` | Backups on disk |
| POST | `/admin/retention?jobs_days=&notifications_days=` | Purge finished jobs and read notifications |
| GET | `/audit/verify` | Recompute the review log's hash chain (admin) |
| GET | `/metrics` (no `/v1`) | Prometheus metrics; `COBOL_METRICS_TOKEN` protects it |

## 8. Errors
```json
{"error": {"code": "RULE_NOT_FOUND", "message": "No rule with id TXNCHK-R999", "request_id": "r_9f2"}}
```
| HTTP | Code | When |
|---|---|---|
| 400 | `INVALID_REQUEST` | Bad parameters or body |
| 401 | `UNAUTHENTICATED` | Missing or invalid token |
| 403 | `FORBIDDEN` | Role does not allow action |
| 404 | `RULE_NOT_FOUND`, `PROGRAM_NOT_FOUND` | Unknown id |
| 409 | `INVALID_STATE` | Illegal status transition |
| 413 | `FILE_TOO_LARGE` | Upload over limit |
| 422 | `PARSE_ERROR`, `NOT_EXPORTABLE` | COBOL could not be parsed; no block fits PMML |
| 403 | `SELF_APPROVAL` | The author of an edit tried to approve it |
| 409 | `ALREADY_APPROVED` | The same reviewer approved this version already |
| 429 | `RATE_LIMITED` | Over the per-minute limit; see `Retry-After` |

## 9. Implementation notes (as built)
- Run with `python -m uvicorn src.api.main:app --port 8000`. The live OpenAPI document is at `/openapi.json` and interactive docs are at `/docs`. The explorer UI is at `/`.
- Roles: `viewer` reads, `reviewer` can also review, assign and comment and read `/v1/audit`, `admin` can also upload, extract, edit the glossary, upload JCL and run backups. Tokens come from `COBOL_API_TOKENS="token:role:user,..."`, from OpenID Connect JWTs (`COBOL_OIDC_*`), or from generated development tokens in `data/dev_tokens.json`. Per-program grants (`data/access.json`) hide other programs (404). See [admin_guide.md](admin_guide.md).
- `approve` needs `COBOL_APPROVALS_REQUIRED` distinct reviewers (default 1); the response's `approvals` lists who approved. The author of an edit cannot approve it.
- A Python client is in `src/sdk.py` (`DecisionClient`), the command line in `src/cli.py` (`cobol2rules`).
- `approve` and `reject` require a non-empty `reason`. `edit` creates a new version: `trace`, `provenance`, `status` and `confidence` cannot be edited.
- Re-extracting unchanged code keeps the rule version and reviewer decisions. Changed code creates a new version and marks an approved predecessor `superseded`.
- Additional endpoints:

| Method | Path | Description |
|---|---|---|
| GET | `/v1/audit?limit=` | Review log, newest first (reviewer) |
| GET | `/v1/rule_sets/{name}/inputs` | Business inputs and outputs of the approved rules of a rule set (builds the scoring form) |
| GET | `/v1/health` | GnuCOBOL backend and LLM availability |

- `POST /v1/score` uses only `approved` rules. Outputs start from the program's `VALUE` defaults, so a decision is returned even when no rule fires.
- `GET /v1/explain/{rule_set}` uses approved rules, or all non-rejected rules when none are approved yet. The response lists the rules used.
