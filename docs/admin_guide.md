# Administration guide

Installing, configuring, securing and operating the service. Settings are
environment variables; nothing secret lives in code or images.

## 1. Deploy

### Docker (recommended)

```bash
docker compose build
COBOL_API_TOKENS="$(openssl rand -hex 16):admin:alice" docker compose up -d
```

The `api` container holds the parser, the GnuCOBOL toolchain (native), the API and
the UI, and runs as an unprivileged user with a health check. The `ollama` container
holds the language models on an internal network with no route out (zero egress).
Copy models into its volume beforehand (for example `~/.ollama/models` from a machine
that already has `qwen2.5-coder:7b`). Without a model the service still extracts and
verifies rules; titles and intents stay empty.

The image installs dependencies from `requirements.lock` with `--require-hashes`, so a
tampered package fails the build. `sbom.cdx.json` (CycloneDX) lists every component.

### Without Docker

```bash
python -m venv .venv && .venv/bin/pip install --require-hashes -r requirements.lock
python scripts/serve.py --host 0.0.0.0 --port 8443        # with COBOL_TLS_CERT / COBOL_TLS_KEY
```

GnuCOBOL is found automatically (`COBOL_BACKEND=auto`): a native `cobc`, else a WSL
distro (`COBOL_WSL_DISTRO`), else Docker with the image built from
`docker/toolchain.Dockerfile` (`docker build -t cobol-toolchain:3.1 -f docker/toolchain.Dockerfile docker`).
The Docker backend mounts only the project and temp folders and gives the container no network.

## 2. TLS

* Direct: `COBOL_TLS_CERT=/path/cert.pem COBOL_TLS_KEY=/path/key.pem python scripts/serve.py`.
  Strict-Transport-Security is sent automatically.
* Behind a reverse proxy that terminates TLS: set `COBOL_TLS=1` so HSTS is still sent.
* Local testing only: `python scripts/dev_cert.py` makes a self-signed certificate for localhost.

Every response carries a strict Content-Security-Policy (same-origin scripts only, no
framing), `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.

## 3. Sign-in and roles

Roles: **viewer** (read, search, export, score) < **reviewer** (approve, reject, edit,
assign, comment) < **admin** (upload, extract, glossary, JCL, backups).

* **Tokens** (service accounts, small teams): `COBOL_API_TOKENS="token:role:user,token:role:user"`.
  Without it, development tokens are generated into `data/dev_tokens.json`; never use those in production.
* **Single sign-on (OpenID Connect)**: set `COBOL_OIDC_ISSUER` (and `COBOL_OIDC_AUDIENCE`).
  The API accepts the identity provider's JWT access tokens (RS256/ES256), checks signature,
  issuer, audience and expiry, and maps roles from a claim:
  `COBOL_OIDC_ROLE_CLAIM` (default `roles`) and `COBOL_OIDC_ROLE_MAP`
  (default `admin=cobol-admin,reviewer=cobol-reviewer,viewer=cobol-viewer`). Groups
  (`COBOL_OIDC_GROUPS_CLAIM`, default `groups`) can also carry the role and drive access grants.

### Per-program access

Without `data/access.json` every user sees every program. With it, non-admin users
see only granted programs (by user or OIDC group, glob patterns on PROGRAM-ID or file name):

```json
{"grants": [{"user": "rita", "programs": ["CRD*", "TXNCHK"]},
            {"group": "loans-team", "programs": ["LON*"]}]}
```

Everything outside a user's grants answers 404 (rules, programs, history, comments,
exports, scoring, repository graph, impact, drift, audit entries).

## 4. Review policy

* `COBOL_APPROVALS_REQUIRED=2` requires two different reviewers per rule version.
  The author of an edit can never approve it.
* `COBOL_WEBHOOK_URL=https://...` receives JSON events (review, assign, comment) for
  chat or ticketing integration. Failures are logged and never block a review.
* The review log is append-only and hash-chained. `GET /v1/audit/verify` (or the
  button in the Audit tab) recomputes the chain and reports the first altered entry.

## 5. Database

* **SQLite** (default): `COBOL_DB=/data/rules.db`. Fine for one server.
* **PostgreSQL**: `COBOL_DB=postgresql://user:password@host:5432/dbname` (install with
  `pip install ".[postgres]"`, already in the image). Tables are created on first start.
  The same tests run against both (`tests/test_postgres.py`).

## 6. Backups and retention

* API: `POST /v1/admin/backup?keep=14` makes an online, consistent copy, checks SQLite
  integrity and the audit hash chain in the copy, and keeps the newest 14.
  `GET /v1/admin/backups` lists them.
* Scheduled: `python scripts/backup.py --keep 30 --purge` (cron or Task Scheduler);
  exit code 1 when the check fails, and older backups are then kept.
* PostgreSQL: backups use `pg_dump` (custom format); restore with `pg_restore`.
* Retention: `POST /v1/admin/retention?jobs_days=30&notifications_days=90` deletes
  finished jobs and read notifications. Rules, versions and the review log are never purged.
* Restore (SQLite): stop the service, copy a backup over `COBOL_DB`, start, then
  `GET /v1/audit/verify`.

## 7. Monitoring

* `GET /v1/health`: toolchain and model availability.
* `GET /metrics`: Prometheus format. Requests and latency per route, rate-limited
  requests, rules by status, programs, jobs, review events. Protect with
  `COBOL_METRICS_TOKEN` (scraper sends `Authorization: Bearer <token>`).
* Logs: `COBOL_LOG_JSON=1` writes one JSON line per request (method, route, status,
  duration, user, request id). Request bodies are never logged, so scoring inputs stay private.
* Every response has `X-Request-ID`; error bodies repeat it for support.

## 8. Rate limits

Per caller (token) and request class, per minute: `COBOL_RATE_LIMIT_PER_MIN` (reads,
default 1200), `COBOL_RATE_LIMIT_WRITE_PER_MIN` (default 120),
`COBOL_RATE_LIMIT_SCORE_PER_MIN` (default 6000). Over the limit: HTTP 429 with `Retry-After`.

## 9. Models

| Model | Use | Notes |
|---|---|---|
| `qwen2.5-coder:7b` (3-shot) | default naming and intents | best measured quality, needs about 5 GB VRAM |
| `cobol-enrich:1.5b-v2` | fine-tuned, smaller and faster | see `results/RESULTS.md` for the comparison |
| `nomic-embed-text` | semantic search | optional; keyword search works without it |

`COBOL_LLM_MODEL` picks one (`none` disables the model). Prompts treat code and
comments as data, outputs are schema-checked, and numbers in intents are checked
against the code.

## 10. Security checklist

- [ ] Real tokens or OIDC; `data/dev_tokens.json` removed
- [ ] TLS (direct or at the proxy with `COBOL_TLS=1`)
- [ ] `COBOL_APPROVALS_REQUIRED` set to your policy
- [ ] `data/access.json` if teams must not see each other's programs
- [ ] `COBOL_METRICS_TOKEN` set or `/metrics` not exposed outside
- [ ] Backups scheduled and a restore tested
- [ ] Model server on the internal network only (compose default)
- [ ] Image built from `requirements.lock` (hash-checked); SBOM archived
