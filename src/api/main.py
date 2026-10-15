"""REST API (docs/api.md) and the decision explorer UI.

    .venv/Scripts/python -m uvicorn src.api.main:app --port 8000

Settings (environment):
  COBOL_DB                 SQLite file (default data/rules.db)
  COBOL_LLM_MODEL          Ollama model for enrichment ("none" = AST names only; default: the best
                           installed of qwen2.5-coder:7b (3-shot) and cobol-enrich:1.5b)
  COBOL_LLM_FEWSHOT        few-shot examples (default 3 for prompted models, 0 for cobol-enrich)
  COBOL_CONSISTENCY_RUNS   extra sampled enrichments for self-consistency (default 2)
  COBOL_API_TOKENS         "token:role:user,..." (default: generated dev tokens)
  COBOL_OIDC_*             single sign-on with JWT access tokens (src/api/auth.py)
  COBOL_ACCESS_FILE        per-program access grants (src/api/access.py; default data/access.json)
  COBOL_APPROVALS_REQUIRED approvals needed before a rule is approved (default 1)
  COBOL_WEBHOOK_URL        review events are POSTed here (optional)
  COBOL_RATE_LIMIT_*       requests per minute (src/api/ops.py); COBOL_METRICS_TOKEN protects /metrics
  COBOL_LOG_JSON=1         structured JSON logs; COBOL_TLS=1 adds HSTS (serve with scripts/serve.py)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.api.access import can_see, patterns
from src.api.auth import OIDC, InvalidToken, User, load_tokens, oidc_user
from src.api.ops import SECURITY_HEADERS, Metrics, RateLimiter, configure_logging, request_class
from src.api.store import Store, StoreError, content_hash
from src.common.models import Rule, dump

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
UI_DIR = ROOT / "src" / "ui" / "static"
MAX_UPLOAD_BYTES = 1_000_000
log = logging.getLogger("cobol.api")
access_log = logging.getLogger("cobol.access")
configure_logging()

app = FastAPI(title="COBOL-to-Decision", version="1.0.0",
              description="Extract source-traceable decision rules from COBOL programs.")
store = Store(os.environ.get("COBOL_DB", str(DATA / "rules.db")))
TOKENS = load_tokens(DATA / "dev_tokens.json")
OIDC_CONFIG = OIDC()
metrics = Metrics()
limiter = RateLimiter()
executor = ThreadPoolExecutor(max_workers=1)        # one extraction at a time: GPU and compiler are shared
_llm_lock = threading.Lock()


# ---- errors --------------------------------------------------------------------------

def _error(status: int, code: str, message: str, request: Request) -> JSONResponse:
    rid = getattr(request.state, "request_id", "r_" + secrets.token_hex(3))
    return JSONResponse({"error": {"code": code, "message": message, "request_id": rid}}, status_code=status)


_NO_CSP = ("/docs", "/redoc", "/openapi.json")     # FastAPI's own docs pages load their assets from a CDN


def _route_template(request: Request) -> str:
    """Route path template (``/v1/rules/{rule_id}``), also for requests refused before routing."""
    from starlette.routing import Match
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    if request.url.path.startswith("/ui"):
        return "/ui/*"
    for r in app.router.routes:
        if getattr(r, "path", None) and r.matches(request.scope)[0] == Match.FULL:
            return r.path
    return "unmatched"


@app.middleware("http")
async def request_id(request: Request, call_next):
    """Request id, rate limit, metrics, security headers and the access log for every request."""
    request.state.request_id = "r_" + secrets.token_hex(3)
    start = time.perf_counter()
    path = request.url.path
    response = None
    if path.startswith("/v1/") and path != "/v1/health":
        auth = request.headers.get("authorization", "")
        key = ("t:" + hashlib.sha256(auth.encode()).hexdigest()[:16]) if auth else \
            ("ip:" + (request.client.host if request.client else "?"))
        cls = request_class(request.method, path)
        wait = limiter.take(key, cls)
        if wait is not None:
            metrics.limited[cls] += 1
            response = _error(429, "RATE_LIMITED", f"Too many requests; retry in {wait:.1f} s", request)
            response.headers["Retry-After"] = str(max(1, math.ceil(wait)))
    if response is None:
        response = await call_next(request)
    template = _route_template(request)
    elapsed = time.perf_counter() - start
    metrics.observe(request.method, template, response.status_code, elapsed)
    for k, v in SECURITY_HEADERS.items():
        if k == "Content-Security-Policy" and path.startswith(_NO_CSP):
            continue
        response.headers.setdefault(k, v)
    if request.url.scheme == "https" or os.environ.get("COBOL_TLS") == "1":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Request-ID"] = request.state.request_id
    access_log.info("%s %s %s %.1fms", request.method, template, response.status_code, elapsed * 1000,
                    extra={"request_id": request.state.request_id, "method": request.method, "route": template,
                           "status": response.status_code, "ms": round(elapsed * 1000, 1),
                           "user": getattr(request.state, "user", None)})
    return response


@app.exception_handler(StoreError)
async def store_error(request: Request, exc: StoreError):
    return _error(exc.status, exc.code, exc.message, request)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    codes = {400: "INVALID_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND",
             409: "INVALID_STATE", 413: "FILE_TOO_LARGE", 422: "PARSE_ERROR"}
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    return _error(exc.status_code, detail.get("code", codes.get(exc.status_code, "ERROR")), detail["message"], request)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return _error(400, "INVALID_REQUEST", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()), request)


def fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


# ---- auth ------------------------------------------------------------------------------

def current_user(request: Request, authorization: Optional[str] = Header(None)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise fail(401, "UNAUTHENTICATED", "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    user = TOKENS.get(token)
    if user is None and OIDC_CONFIG.enabled and token.count(".") == 2:
        try:
            user = oidc_user(OIDC_CONFIG, token)
        except InvalidToken as exc:
            raise fail(401, "UNAUTHENTICATED", f"Invalid token: {exc}") from exc
    if user is None:
        raise fail(401, "UNAUTHENTICATED", "Invalid token")
    request.state.user = user.name
    return user


# ---- per-program access ----------------------------------------------------------------------

def _rule_for(user: User, rule_id: str, version: Optional[int] = None) -> tuple[Rule, str]:
    """The rule, or 404 when the user may not see its program (existence is not leaked)."""
    rule, pid = store.get_rule(rule_id, version)
    if not can_see(user, rule.trace.program, rule_id.rsplit("-R", 1)[0]):
        raise fail(404, "RULE_NOT_FOUND", f"No rule with id {rule_id}")
    return rule, pid


def _program_for(user: User, pid: str) -> dict[str, Any]:
    p = store.program(pid)
    if not can_see(user, p["name"], p["program_file"]):
        raise fail(404, "PROGRAM_NOT_FOUND", f"No program with id {pid}")
    return p


def _visible_rules(user: User, pairs: list[tuple[Rule, str]]) -> list[tuple[Rule, str]]:
    if patterns(user) is None:
        return pairs
    return [(r, p) for r, p in pairs if can_see(user, r.trace.program)]


def need(role: str):
    def check(user: User = Depends(current_user)) -> User:
        if not user.can(role):
            raise fail(403, "FORBIDDEN", f"Role {user.role} cannot do this (needs {role})")
        return user
    return check


# ---- helpers ---------------------------------------------------------------------------

# Default enrichment models in order of measured quality on the held-out test split
# (results/RESULTS.md): prompted 7B first, then the fine-tuned 1.5B (about 3x faster).
DEFAULT_MODELS = [("qwen2.5-coder:7b", 3), ("cobol-enrich:1.5b-v2", 0), ("cobol-enrich:1.5b", 0)]


def llm_settings() -> tuple[Optional[str], int, int]:
    from src.llm.client import OllamaClient
    model = os.environ.get("COBOL_LLM_MODEL")
    fewshot = os.environ.get("COBOL_LLM_FEWSHOT")
    runs = int(os.environ.get("COBOL_CONSISTENCY_RUNS", "2"))
    if model is None:
        for name, shots in DEFAULT_MODELS:
            if OllamaClient(name).available():
                return name, int(fewshot) if fewshot is not None else shots, runs
        return None, 0, 0
    if model.lower() == "none":
        return None, 0, 0
    default_shots = dict(DEFAULT_MODELS).get(model, 0)
    return model, int(fewshot) if fewshot is not None else default_shots, runs


def summary(rule: Rule, pid: str, relevance: Optional[float] = None) -> dict[str, Any]:
    out = {"rule_id": rule.rule_id, "version": rule.version, "title": rule.title, "intent": rule.intent,
           "domain": rule.domain, "concepts": rule.concepts, "status": rule.status,
           "confidence": {"score": rule.confidence.get("score")}, "program_id": pid,
           "external_dependency": rule.external_dependency, "unsupported": rule.unsupported,
           "trace": {"program": rule.trace.program, "paragraph": rule.trace.paragraph,
                     "line_start": rule.trace.line_start, "line_end": rule.trace.line_end}}
    if relevance is not None:
        out["relevance"] = relevance
    return out


def program_dir(pid: str) -> Path:
    return DATA / "uploads" / pid


def _embedder():
    from src.llm.client import embed
    return lambda text: embed([text])[0]


def _rule_text(rule: Rule) -> str:
    from src.rules.search import rule_text
    return rule_text(rule)


# ---- extraction job --------------------------------------------------------------------

def run_extraction(jid: str, pid: str) -> None:
    from src.common.cobol import Toolchain, ToolchainError
    from src.llm.client import OllamaClient, embed
    from src.pipeline import PipelineConfig, run_pipeline

    try:
        prog = store.program(pid)
        folder = program_dir(pid)
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in prog["files"].items():
            (folder / name).write_text(text, encoding="utf-8")
        try:
            toolchain = Toolchain.detect()
        except ToolchainError:
            toolchain = None
        model, fewshot, runs = llm_settings()
        client = OllamaClient(model) if model else None
        if client is not None and not client.available():
            client, model = None, None
        cfg = PipelineConfig(model=model, fewshot=fewshot, consistency_runs=runs if model else 0,
                             cache_dir=DATA / "cache")
        store.update_job(jid, state="running", stage="parse")

        def progress(stage: str, done: int, total: int) -> None:
            store.update_job(jid, stage=stage, slices_done=done, slices_total=total)

        with _llm_lock:
            ex = run_pipeline(folder / prog["program_file"], [folder], cfg, toolchain, client, progress)
        stats = store.save_rules(pid, ex.rules)
        report = ex.report | {"save": stats, "model": model or "ast-only",
                              "toolchain": toolchain.backend if toolchain else None}
        store.set_program_status(pid, "extracted", report)
        try:                                    # best effort: semantic search vectors
            vecs = embed([_rule_text(r) for r in ex.rules]) if ex.rules else []
            for r, v in zip(ex.rules, vecs):
                store.put_embedding(r.rule_id, content_hash(r), v)
        except Exception:
            pass
        store.update_job(jid, state="done", stage="done", slices_done=len(ex.rules), slices_total=len(ex.rules))
        log.info("extracted program=%s rules=%d", pid, len(ex.rules))
    except Exception as exc:                    # surface the error on the job, never crash the worker
        log.exception("extraction failed program=%s", pid)
        store.update_job(jid, state="failed", error=str(exc)[:500])
        store.set_program_status(pid, "failed")


# ---- programs ------------------------------------------------------------------------------

@app.post("/v1/programs", status_code=201)
async def upload_program(program: UploadFile = File(...), copybooks: list[UploadFile] = File(default=[]),
                         user: User = Depends(need("admin"))):
    files: dict[str, str] = {}
    for f in [program] + list(copybooks):
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise fail(413, "FILE_TOO_LARGE", f"{f.filename} is larger than {MAX_UPLOAD_BYTES} bytes")
        name = Path(f.filename or "upload.cbl").name
        files[name] = data.decode("utf-8", errors="replace")
    pname = Path(program.filename or "upload.cbl").name
    from src.analysis.analyze import analyze_program
    tmp = DATA / "uploads" / ("check_" + secrets.token_hex(3))
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for name, text in files.items():
            (tmp / name).write_text(text, encoding="utf-8")
        try:
            analyze_program(tmp / pname, [tmp])
        except Exception as exc:
            raise fail(422, "PARSE_ERROR", f"COBOL could not be parsed: {exc}") from exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    pid = store.add_program(pname.rsplit(".", 1)[0], pname, files)
    return {"program_id": pid, "name": pname, "copybooks": [n for n in files if n != pname], "status": "uploaded"}


@app.get("/v1/programs")
def list_programs(user: User = Depends(need("viewer"))):
    return {"items": [p for p in store.programs() if can_see(user, p["name"], p["program_file"])]}


@app.get("/v1/programs/{pid}")
def get_program(pid: str, user: User = Depends(need("viewer"))):
    p = _program_for(user, pid)
    p["copybooks"] = [n for n in p.pop("files") if n != p["program_file"]]
    return p


@app.get("/v1/programs/{pid}/source")
def program_source(pid: str, user: User = Depends(need("viewer"))):
    _program_for(user, pid)
    from src.ingest.source import load_program
    p = store.program(pid)
    folder = program_dir(pid)
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in p["files"].items():
        (folder / name).write_text(text, encoding="utf-8")
    src = load_program(folder / p["program_file"], [folder])
    return {"program_id": pid, "program_file": p["program_file"], "format": src.fmt, "files": p["files"],
            "resolved": [{"file": l.file, "line": l.line, "text": l.text} for l in src.lines]}


@app.post("/v1/programs/{pid}/extract", status_code=202)
def extract_program(pid: str, user: User = Depends(need("admin"))):
    _program_for(user, pid)
    jid = store.new_job(pid)
    store.set_program_status(pid, "extracting")
    executor.submit(run_extraction, jid, pid)
    return {"job_id": jid, "program_id": pid, "state": "queued"}


@app.get("/v1/jobs/{jid}")
def get_job(jid: str, user: User = Depends(need("viewer"))):
    return store.job(jid)


# ---- rules ------------------------------------------------------------------------------------

@app.get("/v1/rules")
def list_rules(q: Optional[str] = None, concept: Optional[str] = None, program: Optional[str] = None,
               domain: Optional[str] = None, status: Optional[str] = None,
               min_confidence: Optional[float] = Query(None, ge=0, le=1),
               page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
               user: User = Depends(need("viewer"))):
    from src.rules.search import search
    rules = _visible_rules(user, store.latest_rules(program, status, domain, min_confidence, concept))
    embed_query = _embedder() if q else None
    results = search(rules, q, embed_query, lambda r: store.embedding(r.rule_id, content_hash(r)))
    total = len(results)
    start = (page - 1) * page_size
    items = [summary(r, p, s if q else None) for r, p, s in results[start:start + page_size]]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@app.get("/v1/rules/{rule_id}")
def get_rule(rule_id: str, version: Optional[int] = None, user: User = Depends(need("viewer"))):
    rule, pid = _rule_for(user, rule_id, version)
    return dump(rule) | {"program_id": pid}


@app.get("/v1/rules/{rule_id}/trace")
def rule_trace(rule_id: str, context: int = Query(8, ge=0, le=200), user: User = Depends(need("viewer"))):
    rule, pid = _rule_for(user, rule_id)
    p = store.program(pid)
    fname = rule.trace.file or rule.trace.program
    text = p["files"].get(fname) or p["files"].get(p["program_file"], "")
    lines = text.splitlines()
    lo = max(1, rule.trace.line_start - context)
    hi = min(len(lines), rule.trace.line_end + context)
    return {"rule_id": rule_id, "program_id": pid, "file": fname, "trace": rule.trace.model_dump(),
            "lines": [{"n": n, "text": lines[n - 1], "in_rule": rule.trace.line_start <= n <= rule.trace.line_end}
                      for n in range(lo, hi + 1)],
            "total_lines": len(lines)}


class ReviewBody(BaseModel):
    action: str
    reason: str = ""
    changes: Optional[dict[str, Any]] = None


def _webhook(event: dict[str, Any]) -> None:
    """POST review events to COBOL_WEBHOOK_URL (chat / ticketing integration), best effort."""
    url = os.environ.get("COBOL_WEBHOOK_URL")
    if not url:
        return

    def send() -> None:
        try:
            import requests
            requests.post(url, json=event, timeout=3)
        except Exception as exc:                 # never let a notification break a review
            log.warning("webhook failed: %s", exc)
    threading.Thread(target=send, daemon=True).start()


def _review(rule_id: str, body: "ReviewBody", user: User) -> dict[str, Any]:
    if body.action in ("approve", "reject") and not body.reason.strip():
        raise fail(400, "INVALID_REQUEST", "A reason is required for approve and reject")
    _rule_for(user, rule_id)
    rule = store.review(rule_id, body.action, body.reason, user.name, body.changes)
    log.info("review rule=%s action=%s user=%s", rule_id, body.action, user.name)
    a = store.assignment(rule_id)
    for person in {a["assignee"], a["assigned_by"]} - {user.name} if a else ():
        store.notify(person, body.action, rule_id, f"{user.name} chose {body.action} for {rule_id}: {body.reason[:80]}")
    store.db.commit()
    _webhook({"event": "review", "rule_id": rule_id, "action": body.action, "user": user.name,
              "status": rule.status, "version": rule.version})
    return dump(rule) | {"approvals": {"by": store.approvals(rule_id, rule.version),
                                       "required": store.approvals_required}}


@app.patch("/v1/rules/{rule_id}")
def review_rule(rule_id: str, body: ReviewBody, user: User = Depends(need("reviewer"))):
    return _review(rule_id, body, user)


class BulkBody(BaseModel):
    rule_ids: list[str]
    action: str
    reason: str = ""


@app.post("/v1/rules/bulk_review")
def bulk_review(body: BulkBody, user: User = Depends(need("reviewer"))):
    """Approve or reject many rules with one reason; each rule succeeds or fails on its own."""
    if body.action not in ("approve", "reject"):
        raise fail(400, "INVALID_REQUEST", "bulk review supports approve and reject")
    if len(body.rule_ids) > 500:
        raise fail(400, "INVALID_REQUEST", "at most 500 rules per request")
    out = []
    for rid in body.rule_ids:
        try:
            r = _review(rid, ReviewBody(action=body.action, reason=body.reason), user)
            out.append({"rule_id": rid, "ok": True, "status": r["status"]})
        except HTTPException as exc:
            out.append({"rule_id": rid, "ok": False, "error": exc.detail})
        except StoreError as exc:
            out.append({"rule_id": rid, "ok": False, "error": {"code": exc.code, "message": exc.message}})
    return {"items": out, "ok": sum(i["ok"] for i in out), "failed": sum(not i["ok"] for i in out)}


class AssignBody(BaseModel):
    assignee: str


@app.post("/v1/rules/{rule_id}/assign")
def assign_rule(rule_id: str, body: AssignBody, user: User = Depends(need("reviewer"))):
    _rule_for(user, rule_id)
    out = store.assign(rule_id, body.assignee.strip(), user.name)
    _webhook({"event": "assign", "rule_id": rule_id, "assignee": body.assignee, "user": user.name})
    return out


class CommentBody(BaseModel):
    text: str


@app.get("/v1/rules/{rule_id}/comments")
def rule_comments(rule_id: str, user: User = Depends(need("viewer"))):
    _rule_for(user, rule_id)
    return {"items": store.comments(rule_id), "assignment": store.assignment(rule_id)}


@app.post("/v1/rules/{rule_id}/comments", status_code=201)
def add_comment(rule_id: str, body: CommentBody, user: User = Depends(need("reviewer"))):
    _rule_for(user, rule_id)
    text = body.text.strip()
    if not text or len(text) > 4000:
        raise fail(400, "INVALID_REQUEST", "a comment needs 1 to 4000 characters")
    out = store.add_comment(rule_id, user.name, text)
    _webhook({"event": "comment", "rule_id": rule_id, "user": user.name})
    return out


@app.get("/v1/review_queue")
def review_queue(mine: bool = False, user: User = Depends(need("viewer"))):
    items = store.review_queue(user.name if mine else None)
    if patterns(user) is not None:
        items = [i for i in items if can_see(user, i["rule_id"].rsplit("-R", 1)[0])]
    return {"items": items,
            "approvals_required": store.approvals_required}


@app.get("/v1/notifications")
def notifications(unread: bool = False, user: User = Depends(need("viewer"))):
    return {"items": store.notifications(user.name, unread)}


@app.post("/v1/notifications/{nid}/read", status_code=204)
def notification_read(nid: int, user: User = Depends(need("viewer"))):
    store.mark_read(nid, user.name)


@app.get("/v1/audit/verify")
def audit_verify(user: User = Depends(need("admin"))):
    """Recompute the review log's hash chain: detects edited, inserted or deleted entries."""
    return store.verify_audit()


@app.get("/v1/rules/{rule_id}/history")
def rule_history(rule_id: str, user: User = Depends(need("viewer"))):
    _rule_for(user, rule_id)
    return store.history(rule_id)


@app.get("/v1/audit")
def audit(limit: int = Query(100, ge=1, le=1000), user: User = Depends(need("reviewer"))):
    if patterns(user) is not None:
        return {"items": [a for a in store.audit_log(limit) if can_see(user, a["rule_id"].rsplit("-R", 1)[0])]}
    return {"items": store.audit_log(limit)}


@app.get("/v1/conflicts")
def get_conflicts(program: Optional[str] = None, user: User = Depends(need("viewer"))):
    from src.rules.decide import conflicts
    rules = [r for r, _ in _visible_rules(user, store.latest_rules(program)) if r.status != "rejected"]
    return {"items": conflicts(rules)}


# ---- export / score / explain ------------------------------------------------------------------

@app.get("/v1/export")
def export(format: str = Query("json", pattern="^(json|tree|pmml|dmn)$"), program: Optional[str] = None,
           domain: Optional[str] = None, status: Optional[str] = None, user: User = Depends(need("viewer"))):
    """JSON, decision trees, PMML 4.4 or DMN 1.3. XML exports are checked against the official
    schemas before they are returned; skipped rule sets are counted in a response header."""
    from src.rules.dmn import to_dmn
    from src.rules.export import to_json, to_pmml, to_tree_json
    from src.rules.schema_check import validate
    rules = [r for r, _ in _visible_rules(user, store.latest_rules(program, status, domain))]
    if format == "json":
        return JSONResponse(json.loads(to_json(rules)))
    if format == "tree":
        return JSONResponse(to_tree_json(rules))
    xml, skipped = to_pmml(rules) if format == "pmml" else to_dmn(rules, program or "cobol-rules")
    if not xml:
        raise fail(422, "NOT_EXPORTABLE", "no rule set can be expressed in PMML: " + "; ".join(skipped[:5]))
    errors = validate(xml, format)
    if errors:                                   # never hand out a document that breaks the standard
        log.error("%s export failed schema validation: %s", format, errors[:3])
        raise fail(500, "EXPORT_INVALID", f"{format.upper()} failed schema validation: {errors[0]}")
    ext = "pmml" if format == "pmml" else "dmn"
    return PlainTextResponse(xml, media_type="application/xml",
                             headers={"X-Skipped-Rule-Sets": str(len(skipped)),
                                      "Content-Disposition": f"attachment; filename=rules.{ext}"})


def _rule_set(name: str, statuses: tuple[str, ...], user: Optional[User] = None) -> list[Rule]:
    pairs = store.latest_rules(name)
    if user is not None:
        pairs = _visible_rules(user, pairs)
    rules = [r for r, _ in pairs if r.status in statuses]
    return sorted(rules, key=lambda r: (r.trace.program, r.trace.line_start, r.rule_id))


def _defaults(name: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pid in {p for _, p in store.latest_rules(name)}:
        out.update(store.program(pid)["report"].get("defaults", {}))
    return out


class ScoreBody(BaseModel):
    rule_set: str
    inputs: dict[str, Any]


@app.post("/v1/score")
def score_endpoint(body: ScoreBody, user: User = Depends(need("viewer"))):
    """Stateless: inputs are neither stored nor logged (security.md T8)."""
    from src.rules.decide import score
    rules = _rule_set(body.rule_set, ("approved",), user)
    if not rules:
        raise fail(404, "RULE_NOT_FOUND", f"No approved rules for rule set {body.rule_set}")
    return score(rules, body.inputs, _defaults(body.rule_set))


@app.get("/v1/rule_sets/{name}/inputs")
def rule_set_inputs(name: str, user: User = Depends(need("viewer"))):
    from src.rules.decide import input_fields, output_fields
    rules = _rule_set(name, ("approved",), user)
    return {"rule_set": name, "approved_rules": len(rules),
            "inputs": {k: {"kind": v["kind"], "values": v["values"]} for k, v in input_fields(rules).items()},
            "outputs": list(output_fields(rules))}


@app.get("/v1/explain/{rule_set}")
def explain_endpoint(rule_set: str, target: Optional[str] = None, user: User = Depends(need("viewer"))):
    from src.rules.decide import explain
    rules = _rule_set(rule_set, ("approved",), user) or _rule_set(rule_set, ("candidate", "needs_review", "approved"), user)
    if not rules:
        raise fail(404, "RULE_NOT_FOUND", f"No rules for rule set {rule_set}")
    try:
        result = explain(rules, target, defaults=_defaults(rule_set))
    except ValueError as exc:
        raise fail(400, "INVALID_REQUEST", str(exc)) from exc
    return result | {"rule_set": rule_set, "rules_used": [r.rule_id for r in rules],
                     "rule_statuses": sorted({r.status for r in rules})}


# ---- health and UI -----------------------------------------------------------------------------

# ---- repository: JCL, call graph, impact, drift ---------------------------------------------

def _jcl_dir() -> Path:
    d = DATA / "jcl"
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.post("/v1/jcl", status_code=201)
async def upload_jcl(jcl: UploadFile = File(...), user: User = Depends(need("admin"))):
    from src.repo.jcl import parse_jcl
    data = await jcl.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise fail(413, "FILE_TOO_LARGE", f"{jcl.filename} is larger than {MAX_UPLOAD_BYTES} bytes")
    name = Path(jcl.filename or "upload.jcl").name
    if not re.fullmatch(r"[A-Za-z0-9_.@#$-]+", name):
        raise fail(400, "INVALID_REQUEST", "JCL file name may only use letters, digits and _.@#$-")
    text = data.decode("utf-8", errors="replace")
    jobs = parse_jcl(text, name)
    if not jobs:
        raise fail(422, "PARSE_ERROR", "no JOB or EXEC statement found")
    (_jcl_dir() / name).write_text(text, encoding="utf-8")
    return {"name": name, "jobs": [{"job": j.name, "steps": [s.name for s in j.steps]} for j in jobs]}


@app.get("/v1/jcl")
def list_jcl(user: User = Depends(need("viewer"))):
    return {"items": sorted(p.name for p in _jcl_dir().glob("*"))}


_repo_cache: dict[str, Any] = {}


def _repository(user: Optional[User] = None) -> tuple[Any, dict[str, Any]]:
    """Repository graph over every uploaded program the user may see and every JCL member
    (cached until uploads change; users limited to some programs get their own graph)."""
    from src.repo.graph import build_graph, scan_sources
    progs = store.programs()
    if user is not None and patterns(user) is not None:
        progs = [p for p in progs if can_see(user, p["name"], p["program_file"])]
        _repo_cache.pop("key", None)
    jcl = sorted(_jcl_dir().glob("*"))
    key = repr([(p["id"], p.get("sha256")) for p in progs]
               + [(j.name, j.stat().st_mtime) for j in jcl])
    if _repo_cache.get("key") == key:
        return _repo_cache["repo"], _repo_cache["graph"]
    entries, copybooks = [], []
    for p in progs:
        folder = program_dir(p["id"])
        folder.mkdir(parents=True, exist_ok=True)
        prog = store.program(p["id"])
        for name, text in prog["files"].items():
            (folder / name).write_text(text, encoding="utf-8")
        entries.append((folder / prog["program_file"], [folder]))
        copybooks += [Path(n).stem.upper() for n in prog["files"] if n != prog["program_file"]]
    repo = scan_sources(entries, jcl, copybooks, "uploads")
    graph = build_graph(repo)
    _repo_cache.update(key=key, repo=repo, graph=graph)
    return repo, graph


@app.get("/v1/repository")
def repository(user: User = Depends(need("viewer"))):
    """Call graph, copybook use, JCL jobs and datasets across all uploaded sources, with findings."""
    return _repository(user)[1]


@app.get("/v1/impact")
def impact_endpoint(kind: str = Query(..., pattern="^(field|copybook|program|dataset)$"), name: str = Query(..., min_length=1),
                    user: User = Depends(need("viewer"))):
    """What a change would affect: rules that read or write a field, programs including a copybook,
    callers of a program (transitively) and the JCL jobs that run them."""
    from src.repo.graph import impact
    repo, graph = _repository(user)
    return impact(repo, graph, kind, name)


@app.get("/v1/diff")
def drift(old: str, new: str, user: User = Depends(need("viewer"))):
    """Rule drift between two uploads (e.g. before and after a change): added, removed and changed
    rules, with an input where a changed condition behaves differently."""
    from src.pipeline import extract
    from src.rules.diff import diff_rules
    sets = []
    for pid in (old, new):
        prog = _program_for(user, pid)
        folder = program_dir(pid)
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in prog["files"].items():
            (folder / name).write_text(text, encoding="utf-8")
        sets.append(extract(folder / prog["program_file"], [folder]).rules)
    approved = {r.rule_id for r, _ in store.latest_rules(status="approved")}
    before = [r.model_copy(update={"status": "approved"}) if r.rule_id in approved else r for r in sets[0]]
    return {"old": old, "new": new, **diff_rules(before, sets[1])}


class GlossaryBody(BaseModel):
    fields: dict[str, str] = {}
    terms: dict[str, str] = {}


@app.get("/v1/glossary")
def get_glossary(user: User = Depends(need("viewer"))):
    from src.rules.glossary import active
    return active().as_dict()


@app.put("/v1/glossary")
def put_glossary(body: GlossaryBody, user: User = Depends(need("admin"))):
    """Replace the business glossary. Applies to extractions started afterwards."""
    from src.rules.glossary import Glossary, save
    g = Glossary.from_dict(body.model_dump())
    save(g)
    log.info("glossary updated by=%s fields=%d terms=%d", user.name, len(g.fields), len(g.terms))
    return g.as_dict()


# ---- administration: backups and retention ------------------------------------------------------

def _backup_dir() -> Path:
    return Path(os.environ.get("COBOL_BACKUP_DIR") or DATA / "backups")


@app.post("/v1/admin/backup", status_code=201)
def admin_backup(keep: int = Query(14, ge=1, le=365), user: User = Depends(need("admin"))):
    """Verified online backup of the rule store; keeps the newest ``keep`` backups."""
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = store.backup(_backup_dir() / f"rules-{stamp}.db")
    olds = sorted(_backup_dir().glob("rules-*.db"))
    for old in olds[:-keep]:
        old.unlink()
    log.info("backup by=%s ok=%s bytes=%s", user.name, result["ok"], result["bytes"])
    return result | {"path": Path(result["path"]).name, "kept": min(len(olds), keep)}


@app.get("/v1/admin/backups")
def admin_backups(user: User = Depends(need("admin"))):
    return {"items": [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(_backup_dir().glob("rules-*.db"))]}


@app.post("/v1/admin/retention")
def admin_retention(jobs_days: int = Query(30, ge=1), notifications_days: int = Query(90, ge=1),
                    user: User = Depends(need("admin"))):
    """Delete finished jobs and read notifications past their retention period."""
    return store.purge(jobs_days, notifications_days)


@app.get("/metrics", include_in_schema=False)
def metrics_endpoint(authorization: Optional[str] = Header(None)):
    """Prometheus metrics. Set COBOL_METRICS_TOKEN to require ``Authorization: Bearer <token>``."""
    tok = os.environ.get("COBOL_METRICS_TOKEN")
    if tok and not secrets.compare_digest(authorization or "", f"Bearer {tok}"):
        raise fail(401, "UNAUTHENTICATED", "metrics token required")
    s = store.stats()
    gauges = {
        "cobol_rules": ("Latest rule versions by status.", [({"status": k}, v) for k, v in s["rules"].items()]),
        "cobol_programs": ("Uploaded programs.", [({}, s["programs"])]),
        "cobol_jobs": ("Extraction jobs by state.", [({"state": k}, v) for k, v in s["jobs"].items()]),
        "cobol_review_events": ("Entries in the review log.", [({}, s["reviews"])]),
    }
    return PlainTextResponse(metrics.render(gauges), media_type="text/plain; version=0.0.4")


@app.get("/v1/health")
def health():
    from src.common.cobol import Toolchain, ToolchainError
    from src.llm.client import OllamaClient
    try:
        tc = Toolchain.detect().backend
    except ToolchainError:
        tc = None
    model, _, _ = llm_settings()
    return {"status": "ok", "toolchain": tc, "llm_model": model,
            "llm_available": bool(model) and OllamaClient(model).available()}


if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/")
