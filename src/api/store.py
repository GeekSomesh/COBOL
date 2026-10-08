"""SQLite rule store: programs, versioned rules, append-only review log, jobs (implementation.md 7.1).

Integrity (security.md T5): every rule version stores a content hash of its
logic and documentation. Re-extracting unchanged code keeps the current version;
changed code adds a new version and marks an approved predecessor superseded.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.common.models import Rule, dump

SCHEMA = """
CREATE TABLE IF NOT EXISTS programs(
  id TEXT PRIMARY KEY, name TEXT, program_file TEXT, files TEXT, sha256 TEXT, status TEXT,
  report TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS rules(
  rule_id TEXT, version INT, program_id TEXT, json TEXT, status TEXT, confidence REAL,
  concepts TEXT, domain TEXT, title TEXT, content_hash TEXT, created_at TEXT, is_latest INT,
  PRIMARY KEY(rule_id, version));
CREATE INDEX IF NOT EXISTS rules_latest ON rules(is_latest, status);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id TEXT, version INT, action TEXT, reason TEXT,
  user TEXT, changes TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, program_id TEXT, state TEXT, stage TEXT, slices_total INT, slices_done INT,
  error TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS embeddings(
  rule_id TEXT, content_hash TEXT, vector TEXT, PRIMARY KEY(rule_id, content_hash));
"""

TRANSITIONS = {
    "approve": {"candidate", "needs_review"},
    "reject": {"candidate", "needs_review", "approved"},
    "edit": {"candidate", "needs_review", "approved", "rejected"},
}


class StoreError(Exception):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(rule: Rule) -> str:
    d = dump(rule)
    keep = {k: d.get(k) for k in ("conditions", "actions", "else_actions", "title", "intent", "concepts",
                                  "domain", "trace", "external_dependency", "unsupported")}
    return "sha256:" + hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True) if self.path != ":memory:" else None
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---- programs --------------------------------------------------------------
    def add_program(self, name: str, program_file: str, files: dict[str, str]) -> str:
        pid = "p_" + secrets.token_hex(4)
        sha = "sha256:" + hashlib.sha256(files[program_file].encode()).hexdigest()
        with self._lock:
            self.db.execute("INSERT INTO programs VALUES(?,?,?,?,?,?,?,?)",
                            (pid, name, program_file, json.dumps(files), sha, "uploaded", "{}", now()))
            self.db.commit()
        return pid

    def program(self, pid: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM programs WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise StoreError("PROGRAM_NOT_FOUND", f"No program with id {pid}", 404)
        out = dict(row)
        out["files"] = json.loads(out["files"])
        out["report"] = json.loads(out["report"] or "{}")
        counts = self.db.execute(
            "SELECT status, COUNT(*) n FROM rules WHERE program_id=? AND is_latest=1 GROUP BY status", (pid,)).fetchall()
        out["rule_counts"] = {r["status"]: r["n"] for r in counts}
        return out

    def programs(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT id FROM programs ORDER BY created_at DESC").fetchall()
        out = []
        for r in rows:
            p = self.program(r["id"])
            p.pop("files")
            out.append(p)
        return out

    def set_program_status(self, pid: str, status: str, report: Optional[dict] = None) -> None:
        with self._lock:
            self.db.execute("UPDATE programs SET status=?, report=COALESCE(?, report) WHERE id=?",
                            (status, json.dumps(report) if report is not None else None, pid))
            self.db.commit()

    # ---- rules -----------------------------------------------------------------
    def _latest_row(self, rule_id: str) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM rules WHERE rule_id=? AND is_latest=1", (rule_id,)).fetchone()

    def save_rules(self, pid: str, rules: list[Rule]) -> dict[str, int]:
        stats = {"new": 0, "unchanged": 0, "new_version": 0}
        with self._lock:
            for rule in rules:
                h = content_hash(rule)
                prev = self._latest_row(rule.rule_id)
                if prev is not None and prev["content_hash"] == h:
                    # same logic and text: refresh confidence, keep reviewer decisions
                    old = Rule.model_validate_json(prev["json"])
                    if old.status in ("approved", "rejected"):
                        rule = rule.model_copy(update={"status": old.status})
                    rule = rule.model_copy(update={"version": prev["version"]})
                    self.db.execute("UPDATE rules SET json=?, status=?, confidence=? WHERE rule_id=? AND version=?",
                                    (json.dumps(dump(rule)), rule.status, rule.confidence.get("score"),
                                     rule.rule_id, prev["version"]))
                    stats["unchanged"] += 1
                    continue
                version = 1
                if prev is not None:
                    version = prev["version"] + 1
                    self.db.execute("UPDATE rules SET is_latest=0 WHERE rule_id=?", (rule.rule_id,))
                    if prev["status"] == "approved":
                        old = Rule.model_validate_json(prev["json"]).model_copy(update={"status": "superseded"})
                        self.db.execute("UPDATE rules SET status='superseded', json=? WHERE rule_id=? AND version=?",
                                        (json.dumps(dump(old)), rule.rule_id, prev["version"]))
                    stats["new_version"] += 1
                else:
                    stats["new"] += 1
                rule = rule.model_copy(update={"version": version})
                self._insert(pid, rule, h)
            self.db.commit()
        return stats

    def _insert(self, pid: str, rule: Rule, h: str) -> None:
        self.db.execute("INSERT INTO rules VALUES(?,?,?,?,?,?,?,?,?,?,?,1)",
                        (rule.rule_id, rule.version, pid, json.dumps(dump(rule)), rule.status,
                         rule.confidence.get("score"), json.dumps(rule.concepts), rule.domain, rule.title, h, now()))

    def get_rule(self, rule_id: str, version: Optional[int] = None) -> tuple[Rule, str]:
        if version is None:
            row = self._latest_row(rule_id)
        else:
            row = self.db.execute("SELECT * FROM rules WHERE rule_id=? AND version=?", (rule_id, version)).fetchone()
        if row is None:
            raise StoreError("RULE_NOT_FOUND", f"No rule with id {rule_id}", 404)
        return Rule.model_validate_json(row["json"]), row["program_id"]

    def latest_rules(self, program: Optional[str] = None, status: Optional[str] = None,
                     domain: Optional[str] = None, min_confidence: Optional[float] = None,
                     concept: Optional[str] = None) -> list[tuple[Rule, str]]:
        sql = "SELECT r.json, r.program_id FROM rules r JOIN programs p ON p.id = r.program_id WHERE r.is_latest=1"
        args: list[Any] = []
        if program:
            sql += " AND (r.program_id=? OR p.program_file=? OR p.name=? OR r.rule_id LIKE ?)"
            args += [program, program, program, f"{program.split('.')[0]}-R%"]
        if status:
            sql += " AND r.status=?"
            args.append(status)
        if domain:
            sql += " AND r.domain=?"
            args.append(domain)
        if min_confidence is not None:
            sql += " AND COALESCE(r.confidence, 0) >= ?"
            args.append(min_confidence)
        sql += " ORDER BY r.rule_id"
        rows = self.db.execute(sql, args).fetchall()
        out = [(Rule.model_validate_json(r["json"]), r["program_id"]) for r in rows]
        if concept:
            c = concept.lower()
            out = [(r, p) for r, p in out if any(c == x.lower() or c in x.lower() for x in r.concepts)]
        return out

    # ---- reviews ---------------------------------------------------------------
    def review(self, rule_id: str, action: str, reason: str, user: str,
               changes: Optional[dict[str, Any]] = None) -> Rule:
        if action not in TRANSITIONS:
            raise StoreError("INVALID_REQUEST", f"Unknown action {action!r}", 400)
        with self._lock:
            rule, pid = self.get_rule(rule_id)
            if rule.status not in TRANSITIONS[action]:
                raise StoreError("INVALID_STATE", f"Cannot {action} a rule in status {rule.status}", 409)
            if action == "edit":
                if not changes:
                    raise StoreError("INVALID_REQUEST", "edit needs a 'changes' object", 400)
                forbidden = set(changes) & {"rule_id", "version", "trace", "provenance", "status", "confidence"}
                if forbidden:
                    raise StoreError("INVALID_REQUEST", f"cannot edit {sorted(forbidden)}", 400)
                data = dump(rule) | changes
                data["version"] = rule.version + 1
                data["status"] = "needs_review" if rule.status in ("approved", "rejected") else rule.status
                data["provenance"] = {**rule.provenance, "edited_by": user, "edited_at": now()}
                try:
                    new = Rule.model_validate(data)
                except Exception as exc:
                    raise StoreError("INVALID_REQUEST", f"edited rule is invalid: {exc}", 400) from exc
                self.db.execute("UPDATE rules SET is_latest=0 WHERE rule_id=?", (rule_id,))
                self._insert(pid, new, content_hash(new))
                result = new
            else:
                status = "approved" if action == "approve" else "rejected"
                result = rule.model_copy(update={"status": status})
                self.db.execute("UPDATE rules SET status=?, json=? WHERE rule_id=? AND version=?",
                                (status, json.dumps(dump(result)), rule_id, rule.version))
            self.db.execute("INSERT INTO reviews(rule_id, version, action, reason, user, changes, ts) VALUES(?,?,?,?,?,?,?)",
                            (rule_id, result.version, action, reason, user, json.dumps(changes or {}), now()))
            self.db.commit()
        return result

    def history(self, rule_id: str) -> dict[str, Any]:
        versions = self.db.execute(
            "SELECT version, status, confidence, content_hash, created_at, is_latest FROM rules WHERE rule_id=? ORDER BY version",
            (rule_id,)).fetchall()
        if not versions:
            raise StoreError("RULE_NOT_FOUND", f"No rule with id {rule_id}", 404)
        reviews = self.db.execute("SELECT * FROM reviews WHERE rule_id=? ORDER BY id", (rule_id,)).fetchall()
        return {"rule_id": rule_id, "versions": [dict(v) for v in versions],
                "reviews": [dict(r) | {"changes": json.loads(r["changes"] or "{}")} for r in reviews]}

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) | {"changes": json.loads(r["changes"] or "{}")} for r in rows]

    # ---- jobs ------------------------------------------------------------------
    def new_job(self, pid: str) -> str:
        jid = "j_" + secrets.token_hex(4)
        with self._lock:
            self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
                            (jid, pid, "queued", "queued", 0, 0, None, now(), now()))
            self.db.commit()
        return jid

    def update_job(self, jid: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
        with self._lock:
            self.db.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), now(), jid))
            self.db.commit()

    def job(self, jid: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if row is None:
            raise StoreError("JOB_NOT_FOUND", f"No job with id {jid}", 404)
        return {"job_id": row["id"], "program_id": row["program_id"], "state": row["state"], "stage": row["stage"],
                "slices_total": row["slices_total"], "slices_done": row["slices_done"], "error": row["error"],
                "updated_at": row["updated_at"]}

    # ---- embeddings cache -----------------------------------------------------------
    def embedding(self, rule_id: str, h: str) -> Optional[list[float]]:
        row = self.db.execute("SELECT vector FROM embeddings WHERE rule_id=? AND content_hash=?", (rule_id, h)).fetchone()
        return json.loads(row["vector"]) if row else None

    def put_embedding(self, rule_id: str, h: str, vec: list[float]) -> None:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO embeddings VALUES(?,?,?)", (rule_id, h, json.dumps(vec)))
            self.db.commit()
