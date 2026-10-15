"""The rule store on PostgreSQL (skipped unless data/pg_test.json or COBOL_TEST_PG_URL names a test database).

Runs the same API flows as the SQLite store: extraction, versioned rules, the review
workflow with two approvers, comments and notifications, the hash-chained audit log.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.conftest import ROOT

TABLES = ["programs", "rules", "reviews", "jobs", "embeddings", "approvals", "assignments", "comments", "notifications"]


def _config():
    url = os.environ.get("COBOL_TEST_PG_URL")
    cfg = ROOT / "data" / "pg_test.json"
    data = json.loads(cfg.read_text()) if cfg.exists() else {}
    return url or data.get("url"), data.get("keepalive")


@pytest.fixture(scope="module")
def pg_url():
    """The test database URL; starts the configured keep-alive (e.g. Postgres inside WSL) for the module."""
    import subprocess
    import time
    url, keepalive = _config()
    if not url:
        pytest.skip("no PostgreSQL test database configured")
    proc = subprocess.Popen(keepalive, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) if keepalive else None
    try:
        import psycopg
        for _ in range(30):
            try:
                psycopg.connect(url, connect_timeout=3).close()
                break
            except Exception:
                time.sleep(1)
        yield url
    finally:
        if proc is not None:
            proc.terminate()


@pytest.fixture
def pg_client(pg_url, tmp_path, monkeypatch):
    url = pg_url
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=3, autocommit=True) as c:
            for t in TABLES:
                c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    except Exception as exc:
        pytest.skip(f"PostgreSQL test database not reachable: {exc}")
    from fastapi.testclient import TestClient
    from src.api import main
    from src.api.auth import User
    from src.api.store import Store
    store = Store(url)
    monkeypatch.setenv("COBOL_LLM_MODEL", "none")
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "TOKENS", {"t-admin": User("ann", "admin"), "t-rev": User("rita", "reviewer"),
                                         "t-view": User("vic", "viewer")})
    monkeypatch.setattr(main, "DATA", tmp_path)
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(main, "executor", pool)
    yield TestClient(main.app), store
    pool.shutdown(wait=True)
    store.close()


def test_api_flows_on_postgres(pg_client):
    from tests.test_api import FIXTURES, H, upload_and_extract
    client, store = pg_client
    assert store.postgres
    pid = upload_and_extract(client, FIXTURES / "FLOWPRG.cob")
    rules = client.get("/v1/rules", params={"program": pid, "page_size": 200}, headers=H["viewer"]).json()["items"]
    assert len(rules) >= 4
    rid, other = rules[0]["rule_id"], rules[1]["rule_id"]
    store.approvals_required = 2
    client.post(f"/v1/rules/{rid}/assign", json={"assignee": "rita"}, headers=H["admin"])
    client.post(f"/v1/rules/{rid}/assign", json={"assignee": "rita"}, headers=H["admin"])    # upsert
    c = client.post(f"/v1/rules/{rid}/comments", json={"text": "checked against policy"}, headers=H["reviewer"]).json()
    assert c["id"] >= 1
    assert client.get("/v1/notifications", headers=H["reviewer"]).json()["items"][0]["kind"] == "assigned"
    r = client.patch(f"/v1/rules/{rid}", json={"action": "approve", "reason": "ok"}, headers=H["reviewer"]).json()
    assert r["status"] != "approved"
    r = client.patch(f"/v1/rules/{rid}", json={"action": "approve", "reason": "ok"}, headers=H["admin"]).json()
    assert r["status"] == "approved"
    e = client.patch(f"/v1/rules/{other}", json={"action": "edit", "changes": {"title": "Renamed"}}, headers=H["reviewer"])
    assert e.json()["version"] == 2
    assert client.patch(f"/v1/rules/{other}", json={"action": "approve", "reason": "x"},
                        headers=H["reviewer"]).status_code == 403
    hist = client.get(f"/v1/rules/{other}/history", headers=H["viewer"]).json()
    assert [v["version"] for v in hist["versions"]] == [1, 2]
    assert client.get("/v1/audit/verify", headers=H["admin"]).json()["ok"] is True
    assert "cobol_rules{" in client.get("/metrics").text
    assert client.get("/v1/export", params={"format": "dmn"}, headers=H["viewer"]).status_code == 200
    # re-extracting unchanged code keeps versions and reviewer decisions
    from tests.test_api import wait_job
    wait_job(client, client.post(f"/v1/programs/{pid}/extract", headers=H["admin"]).json())
    assert client.get(f"/v1/rules/{rid}", headers=H["viewer"]).json()["status"] == "approved"
