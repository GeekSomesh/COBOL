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
| GET | `/conflicts` | Duplicate, overlapping or conflicting rule pairs *(stretch)* |

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
| GET | `/export?format=pmml` | PMML TreeModel for tree-shaped rule sets |
| GET | `/export/report?program={id}` | Audit report (PDF) *(stretch)* |

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

## 6. Errors
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
| 422 | `PARSE_ERROR` | COBOL could not be parsed |
