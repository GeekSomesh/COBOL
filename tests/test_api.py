"""API end-to-end: upload -> extract job -> search -> trace -> review -> export -> score (AST-only, no LLM)."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from src.api import main
from src.api.auth import User
from src.api.store import Store
from tests.conftest import ROOT, TXNCHK

FIXTURES = ROOT / "tests" / "fixtures" / "cobol"
H = {"admin": {"Authorization": "Bearer t-admin"}, "reviewer": {"Authorization": "Bearer t-rev"},
     "viewer": {"Authorization": "Bearer t-view"}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COBOL_LLM_MODEL", "none")
    monkeypatch.setattr(main, "store", Store(tmp_path / "rules.db"))
    monkeypatch.setattr(main, "TOKENS", {"t-admin": User("ann", "admin"), "t-rev": User("rita", "reviewer"),
                                         "t-view": User("vic", "viewer")})
    monkeypatch.setattr(main, "DATA", tmp_path)
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(main, "executor", pool)
    yield TestClient(main.app)
    pool.shutdown(wait=True)          # no job may outlive its test's database


def upload_and_extract(client, path, copybooks=()):
    files = [("program", (path.name, path.read_bytes()))] + [("copybooks", (c.name, c.read_bytes())) for c in copybooks]
    r = client.post("/v1/programs", files=files, headers=H["admin"])
    assert r.status_code == 201, r.text
    pid = r.json()["program_id"]
    wait_job(client, client.post(f"/v1/programs/{pid}/extract", headers=H["admin"]).json())
    return pid


def wait_job(client, job):
    for _ in range(300):
        state = client.get(f"/v1/jobs/{job['job_id']}", headers=H["viewer"]).json()
        if state["state"] in ("done", "failed"):
            break
        time.sleep(0.2)
    assert state["state"] == "done", state


def test_auth_and_roles(client):
    assert client.get("/v1/rules").status_code == 401
    r = client.get("/v1/rules", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    r = client.post("/v1/programs", files=[("program", ("X.cob", b"x"))], headers=H["viewer"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_end_to_end_flow(client):
    pid = upload_and_extract(client, TXNCHK)
    prog = client.get(f"/v1/programs/{pid}", headers=H["viewer"]).json()
    assert prog["status"] == "extracted" and sum(prog["rule_counts"].values()) == 1

    hits = client.get("/v1/rules", params={"q": "customers under 18"}, headers=H["viewer"]).json()
    assert hits["total"] == 1 and hits["items"][0]["rule_id"] == "TXNCHK-R001"
    assert client.get("/v1/rules", params={"q": "interest rate for savings"}, headers=H["viewer"]).json()["total"] == 0

    trace = client.get("/v1/rules/TXNCHK-R001/trace", headers=H["viewer"]).json()
    assert [l["n"] for l in trace["lines"] if l["in_rule"]] == [16, 17, 18]

    # viewers cannot review, a reason is required, approve works once
    assert client.patch("/v1/rules/TXNCHK-R001", json={"action": "approve", "reason": "ok"}, headers=H["viewer"]).status_code == 403
    assert client.patch("/v1/rules/TXNCHK-R001", json={"action": "approve"}, headers=H["reviewer"]).status_code == 400
    r = client.patch("/v1/rules/TXNCHK-R001", json={"action": "approve", "reason": "matches policy 4"}, headers=H["reviewer"])
    assert r.status_code == 200 and r.json()["status"] == "approved"
    again = client.patch("/v1/rules/TXNCHK-R001", json={"action": "approve", "reason": "x"}, headers=H["reviewer"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATE"
    hist = client.get("/v1/rules/TXNCHK-R001/history", headers=H["viewer"]).json()
    assert hist["reviews"][0]["user"] == "rita" and hist["reviews"][0]["reason"] == "matches policy 4"

    # stateless scoring with approved rules only
    s = client.post("/v1/score", json={"rule_set": "TXNCHK", "inputs": {"cust_age": 17, "txn_amount": 7500}},
                    headers=H["viewer"]).json()
    assert s["decision"] == {"flag_review": "Y"} and s["fired_rules"] == ["TXNCHK-R001"]
    s = client.post("/v1/score", json={"rule_set": "TXNCHK", "inputs": {"cust_age": 30, "txn_amount": 7500}},
                    headers=H["viewer"]).json()
    assert s["decision"] == {"flag_review": "N"} and s["fired_rules"] == []

    exported = client.get("/v1/export", params={"format": "json", "status": "approved"}, headers=H["viewer"]).json()
    assert [r["rule_id"] for r in exported] == ["TXNCHK-R001"]
    tree = client.get("/v1/export", params={"format": "tree"}, headers=H["viewer"]).json()
    assert tree[0]["tree"]["true_branch"]["leaf"][0]["value"] == "Y"
    pmml = client.get("/v1/export", params={"format": "pmml"}, headers=H["viewer"])
    assert pmml.status_code == 200 and "<TreeModel" in pmml.text


def test_reextraction_keeps_decisions_and_edit_versions(client):
    pid = upload_and_extract(client, TXNCHK)
    client.patch("/v1/rules/TXNCHK-R001", json={"action": "approve", "reason": "ok"}, headers=H["reviewer"])
    wait_job(client, client.post(f"/v1/programs/{pid}/extract", headers=H["admin"]).json())
    r = client.get("/v1/rules/TXNCHK-R001", headers=H["viewer"]).json()
    assert r["status"] == "approved" and r["version"] == 1          # unchanged code: same version, decision kept
    e = client.patch("/v1/rules/TXNCHK-R001", json={"action": "edit", "reason": "clarify",
                                                    "changes": {"title": "Minor high-value review"}}, headers=H["reviewer"])
    assert e.json()["version"] == 2 and e.json()["status"] == "needs_review"
    bad = client.patch("/v1/rules/TXNCHK-R001", json={"action": "edit", "reason": "x", "changes": {"trace": {}}},
                       headers=H["reviewer"])
    assert bad.status_code == 400


def test_upload_limits_and_parse_errors(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    r = client.post("/v1/programs", files=[("program", ("BIG.cob", b"x" * 11))], headers=H["admin"])
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_copybooks_conflicts_and_explain(client):
    upload_and_extract(client, FIXTURES / "FIXEDPRG.cbl", [FIXTURES / "CUSTREC.cpy"])
    upload_and_extract(client, TXNCHK)
    items = client.get("/v1/rules", params={"program": "FIXEDPRG.cbl"}, headers=H["viewer"]).json()["items"]
    assert len(items) == 1 and items[0]["trace"]["line_start"] == 13
    assert client.get("/v1/conflicts", headers=H["viewer"]).status_code == 200
    ex = client.get("/v1/explain/TXNCHK.cob", headers=H["viewer"]).json()
    assert "features" in ex, ex
    assert {f["name"] for f in ex["features"]} == {"cust_age", "txn_amount"}
    assert ex["surrogate_fidelity"] > 0.9


def test_glossary_is_admin_edited_and_readable(client, tmp_path, monkeypatch):
    monkeypatch.setenv("COBOL_GLOSSARY", str(tmp_path / "glossary.json"))
    assert client.get("/v1/glossary", headers=H["viewer"]).json() == {"fields": {}, "terms": {}}
    body = {"fields": {"ws-cscr": "Credit Score"}, "terms": {"amt": "Amount"}}
    assert client.put("/v1/glossary", json=body, headers=H["reviewer"]).status_code == 403
    r = client.put("/v1/glossary", json=body, headers=H["admin"])
    assert r.status_code == 200 and r.json() == {"fields": {"WS-CSCR": "credit_score"}, "terms": {"AMT": "amount"}}
    assert client.get("/v1/glossary", headers=H["viewer"]).json()["fields"] == {"WS-CSCR": "credit_score"}


def test_repository_jcl_impact_and_drift(client):
    ibm = ROOT / "data" / "public" / "ibm-zopeneditor-sample"
    books = [("copybooks", (n, (ibm / n).read_bytes())) for n in ("CUSTCOPY.cpy", "TRANREC.cpy")]
    ids = {}
    for name in ("SAM1.cbl", "SAM2.cbl"):
        r = client.post("/v1/programs", files=[("program", (name, (ibm / name).read_bytes()))] + books, headers=H["admin"])
        ids[name] = r.json()["program_id"]
    assert client.post("/v1/jcl", files=[("jcl", ("RUN.jcl", (ibm / "RUN.jcl").read_bytes()))],
                       headers=H["viewer"]).status_code == 403
    r = client.post("/v1/jcl", files=[("jcl", ("RUN.jcl", (ibm / "RUN.jcl").read_bytes()))], headers=H["admin"])
    assert r.status_code == 201 and r.json()["jobs"][0]["job"] == "ZDERUN"
    g = client.get("/v1/repository", headers=H["viewer"]).json()
    assert any(e["type"] == "CALLS" and e["to"] == "program:SAM2" for e in g["edges"])
    imp = client.get("/v1/impact", params={"kind": "program", "name": "SAM2"}, headers=H["viewer"]).json()
    assert imp["callers"] == ["SAM1"] and imp["jobs"] == ["ZDERUN"]
    changed = (ibm / "SAM2.cbl").read_text().replace("IF TRAN-UPDATE-NUMX1 = '+'", "IF TRAN-UPDATE-NUMX1 = '#'", 1)
    r = client.post("/v1/programs", files=[("program", ("SAM2.cbl", changed.encode()))] + books, headers=H["admin"])
    d = client.get("/v1/diff", params={"old": ids["SAM2.cbl"], "new": r.json()["program_id"]}, headers=H["viewer"]).json()
    assert d["summary"]["changed"] == 1 and d["changed"][0]["changes"] == ["TRAN-UPDATE-NUMX1 ==: + -> #"]


def test_review_workflow_two_approvers_assignment_comments_bulk_and_audit_chain(client, monkeypatch):
    pid = upload_and_extract(client, FIXTURES / "FLOWPRG.cob")
    main.store.approvals_required = 2
    rules = client.get("/v1/rules", params={"program": pid}, headers=H["viewer"]).json()["items"]
    rid = rules[0]["rule_id"]
    # assignment notifies the assignee; comments notify the people involved
    assert client.post(f"/v1/rules/{rid}/assign", json={"assignee": "rita"}, headers=H["admin"]).json()["assignee"] == "rita"
    client.post(f"/v1/rules/{rid}/comments", json={"text": "threshold matches policy 4.2?"}, headers=H["reviewer"])
    notes = client.get("/v1/notifications", headers=H["reviewer"]).json()["items"]
    assert [n["kind"] for n in notes] == ["assigned"]
    assert client.get("/v1/notifications", headers=H["admin"]).json()["items"][0]["kind"] == "comment"
    assert client.get(f"/v1/rules/{rid}/comments", headers=H["viewer"]).json()["items"][0]["user"] == "rita"
    # two distinct approvals are needed; the same reviewer cannot approve twice
    r = client.patch(f"/v1/rules/{rid}", json={"action": "approve", "reason": "ok"}, headers=H["reviewer"]).json()
    assert r["status"] != "approved" and r["approvals"] == {"by": ["rita"], "required": 2}
    assert client.patch(f"/v1/rules/{rid}", json={"action": "approve", "reason": "again"},
                        headers=H["reviewer"]).status_code == 409
    r = client.patch(f"/v1/rules/{rid}", json={"action": "approve", "reason": "ok"}, headers=H["admin"]).json()
    assert r["status"] == "approved"
    # four eyes: whoever edits a rule cannot approve that version
    other = rules[1]["rule_id"]
    client.patch(f"/v1/rules/{other}", json={"action": "edit", "changes": {"title": "Renamed"}}, headers=H["reviewer"])
    r = client.patch(f"/v1/rules/{other}", json={"action": "approve", "reason": "mine"}, headers=H["reviewer"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "SELF_APPROVAL"
    # bulk review reports each rule on its own
    ids = [x["rule_id"] for x in rules[2:4]] + ["NOPE-R001"]
    b = client.post("/v1/rules/bulk_review", json={"rule_ids": ids, "action": "reject", "reason": "dup"},
                    headers=H["reviewer"]).json()
    assert b["ok"] == 2 and b["failed"] == 1
    queue = client.get("/v1/review_queue", headers=H["viewer"]).json()
    assert queue["approvals_required"] == 2 and all(i["status"] in ("needs_review", "candidate") for i in queue["items"])
    # the review log is hash-chained: tampering with an entry is detected
    assert client.get("/v1/audit/verify", headers=H["admin"]).json()["ok"] is True
    main.store.db.execute("UPDATE reviews SET reason='changed later' WHERE id=(SELECT MIN(id) FROM reviews)")
    v = client.get("/v1/audit/verify", headers=H["admin"]).json()
    assert v["ok"] is False and v["broken_at"] >= 1
