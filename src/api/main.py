"""REST API (docs/api.md) and the decision explorer UI.

    .venv/Scripts/python -m uvicorn src.api.main:app --port 8000

Settings (environment):
  COBOL_DB                 SQLite file (default data/rules.db)
  COBOL_LLM_MODEL          Ollama model for enrichment ("none" = AST names only; default: the best
                           installed of qwen2.5-coder:7b (3-shot) and cobol-enrich:1.5b)
  COBOL_LLM_FEWSHOT        few-shot examples (default 3 for prompted models, 0 for cobol-enrich)
  COBOL_CONSISTENCY_RUNS   extra sampled enrichments for self-consistency (default 2)
  COBOL_API_TOKENS         "token:role:user,..." (default: generated dev tokens)
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.api.auth import User, load_tokens
from src.api.store import Store, StoreError, content_hash
from src.common.models import Rule, dump

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
UI_DIR = ROOT / "src" / "ui" / "static"
MAX_UPLOAD_BYTES = 1_000_000
log = logging.getLogger("cobol.api")

app = FastAPI(title="COBOL-to-Decision", version="1.0.0",
              description="Extract source-traceable decision rules from COBOL programs.")
store = Store(os.environ.get("COBOL_DB", str(DATA / "rules.db")))
TOKENS = load_tokens(DATA / "dev_tokens.json")
executor = ThreadPoolExecutor(max_workers=1)        # one extraction at a time: GPU and compiler are shared
_llm_lock = threading.Lock()


# ---- errors --------------------------------------------------------------------------

def _error(status: int, code: str, message: str, request: Request) -> JSONResponse:
    rid = getattr(request.state, "request_id", "r_" + secrets.token_hex(3))
    return JSONResponse({"error": {"code": code, "message": message, "request_id": rid}}, status_code=status)


@app.middleware("http")
async def request_id(request: Request, call_next):
    request.state.request_id = "r_" + secrets.token_hex(3)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
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

def current_user(authorization: Optional[str] = Header(None)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise fail(401, "UNAUTHENTICATED", "Missing bearer token")
    user = TOKENS.get(authorization.split(" ", 1)[1].strip())
    if user is None:
        raise fail(401, "UNAUTHENTICATED", "Invalid token")
    return user


def need(role: str):
    def check(user: User = Depends(current_user)) -> User:
        if not user.can(role):
            raise fail(403, "FORBIDDEN", f"Role {user.role} cannot do this (needs {role})")
        return user
    return check


# ---- helpers ---------------------------------------------------------------------------

# Default enrichment models in order of measured quality on the held-out test split
# (results/RESULTS.md): prompted 7B first, then the fine-tuned 1.5B (about 3x faster).
DEFAULT_MODELS = [("qwen2.5-coder:7b", 3), ("cobol-enrich:1.5b", 0)]


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
    return {"items": store.programs()}


@app.get("/v1/programs/{pid}")
def get_program(pid: str, user: User = Depends(need("viewer"))):
    p = store.program(pid)
    p["copybooks"] = [n for n in p.pop("files") if n != p["program_file"]]
    return p


@app.get("/v1/programs/{pid}/source")
def program_source(pid: str, user: User = Depends(need("viewer"))):
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
    store.program(pid)
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
    rules = store.latest_rules(program, status, domain, min_confidence, concept)
    embed_query = _embedder() if q else None
    results = search(rules, q, embed_query, lambda r: store.embedding(r.rule_id, content_hash(r)))
    total = len(results)
    start = (page - 1) * page_size
    items = [summary(r, p, s if q else None) for r, p, s in results[start:start + page_size]]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@app.get("/v1/rules/{rule_id}")
def get_rule(rule_id: str, version: Optional[int] = None, user: User = Depends(need("viewer"))):
    rule, pid = store.get_rule(rule_id, version)
    return dump(rule) | {"program_id": pid}


@app.get("/v1/rules/{rule_id}/trace")
def rule_trace(rule_id: str, context: int = Query(8, ge=0, le=200), user: User = Depends(need("viewer"))):
    rule, pid = store.get_rule(rule_id)
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


@app.patch("/v1/rules/{rule_id}")
def review_rule(rule_id: str, body: ReviewBody, user: User = Depends(need("reviewer"))):
    if body.action in ("approve", "reject") and not body.reason.strip():
        raise fail(400, "INVALID_REQUEST", "A reason is required for approve and reject")
    rule = store.review(rule_id, body.action, body.reason, user.name, body.changes)
    log.info("review rule=%s action=%s user=%s", rule_id, body.action, user.name)
    return dump(rule)


@app.get("/v1/rules/{rule_id}/history")
def rule_history(rule_id: str, user: User = Depends(need("viewer"))):
    return store.history(rule_id)


@app.get("/v1/audit")
def audit(limit: int = Query(100, ge=1, le=1000), user: User = Depends(need("reviewer"))):
    return {"items": store.audit_log(limit)}


@app.get("/v1/conflicts")
def get_conflicts(program: Optional[str] = None, user: User = Depends(need("viewer"))):
    from src.rules.decide import conflicts
    rules = [r for r, _ in store.latest_rules(program) if r.status != "rejected"]
    return {"items": conflicts(rules)}


# ---- export / score / explain ------------------------------------------------------------------

@app.get("/v1/export")
def export(format: str = Query("json", pattern="^(json|tree|pmml)$"), program: Optional[str] = None,
           domain: Optional[str] = None, status: Optional[str] = None, user: User = Depends(need("viewer"))):
    from src.rules.export import to_json, to_pmml, to_tree_json
    rules = [r for r, _ in store.latest_rules(program, status, domain)]
    if format == "json":
        return JSONResponse(json.loads(to_json(rules)))
    if format == "tree":
        return JSONResponse(to_tree_json(rules))
    xml, skipped = to_pmml(rules)
    return PlainTextResponse(xml, media_type="application/xml",
                             headers={"X-PMML-Skipped": str(len(skipped)),
                                      "Content-Disposition": "attachment; filename=rules.pmml"})


def _rule_set(name: str, statuses: tuple[str, ...]) -> list[Rule]:
    rules = [r for r, _ in store.latest_rules(name) if r.status in statuses]
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
    rules = _rule_set(body.rule_set, ("approved",))
    if not rules:
        raise fail(404, "RULE_NOT_FOUND", f"No approved rules for rule set {body.rule_set}")
    return score(rules, body.inputs, _defaults(body.rule_set))


@app.get("/v1/rule_sets/{name}/inputs")
def rule_set_inputs(name: str, user: User = Depends(need("viewer"))):
    from src.rules.decide import input_fields, output_fields
    rules = _rule_set(name, ("approved",))
    return {"rule_set": name, "approved_rules": len(rules),
            "inputs": {k: {"kind": v["kind"], "values": v["values"]} for k, v in input_fields(rules).items()},
            "outputs": list(output_fields(rules))}


@app.get("/v1/explain/{rule_set}")
def explain_endpoint(rule_set: str, target: Optional[str] = None, user: User = Depends(need("viewer"))):
    from src.rules.decide import explain
    rules = _rule_set(rule_set, ("approved",)) or _rule_set(rule_set, ("candidate", "needs_review", "approved"))
    if not rules:
        raise fail(404, "RULE_NOT_FOUND", f"No rules for rule set {rule_set}")
    try:
        result = explain(rules, target, defaults=_defaults(rule_set))
    except ValueError as exc:
        raise fail(400, "INVALID_REQUEST", str(exc)) from exc
    return result | {"rule_set": rule_set, "rules_used": [r.rule_id for r in rules],
                     "rule_statuses": sorted({r.status for r in rules})}


# ---- health and UI -----------------------------------------------------------------------------

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
