"""OIDC tokens, per-program access, rate limits, metrics and security headers."""

import json
import time

import pytest

from src.api import main
from src.api.auth import OIDC, InvalidToken, User
from tests.test_api import FIXTURES, H, TXNCHK, client, upload_and_extract  # noqa: F401  (fixture)


class _Key:
    def __init__(self, key):
        self.key = key


class _Jwks:
    def __init__(self, public):
        self.public = public

    def get_signing_key_from_jwt(self, token):
        return _Key(self.public)


@pytest.fixture(scope="module")
def keys():
    from cryptography.hazmat.primitives.asymmetric import rsa
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def _token(private, **claims):
    import jwt
    base = {"iss": "https://idp.example", "aud": "cobol-api", "exp": int(time.time()) + 300,
            "preferred_username": "olga", "roles": ["cobol-reviewer"]}
    return jwt.encode(base | claims, private, algorithm="RS256")


def _oidc(public):
    env = {"COBOL_OIDC_ISSUER": "https://idp.example", "COBOL_OIDC_AUDIENCE": "cobol-api"}
    return OIDC(env, jwks=_Jwks(public))


def test_oidc_validates_signature_issuer_audience_expiry_and_roles(keys):
    private, public = keys
    oidc = _oidc(public)
    u = oidc.user(_token(private, groups=["loans-team"]))
    assert u == User("olga", "reviewer", ("loans-team",))
    from cryptography.hazmat.primitives.asymmetric import rsa
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for bad in (_token(other), _token(private, iss="https://evil"), _token(private, aud="other"),
                _token(private, exp=int(time.time()) - 600), _token(private, roles=["nothing"])):
        with pytest.raises(InvalidToken):
            oidc.user(bad)


def test_oidc_tokens_work_through_the_api(client, keys, monkeypatch):
    private, public = keys
    monkeypatch.setattr(main, "OIDC_CONFIG", _oidc(public))
    upload_and_extract(client, TXNCHK)
    r = client.get("/v1/rules", headers={"Authorization": "Bearer " + _token(private)})
    assert r.status_code == 200 and r.json()["total"] >= 1
    r = client.get("/v1/rules", headers={"Authorization": "Bearer " + _token(private, exp=int(time.time()) - 600)})
    assert r.status_code == 401


def test_per_program_access_hides_other_programs(client, tmp_path, monkeypatch):
    upload_and_extract(client, TXNCHK)
    upload_and_extract(client, FIXTURES / "FLOWPRG.cob")
    grants = tmp_path / "access.json"
    grants.write_text(json.dumps({"grants": [{"user": "vic", "programs": ["FLOW*"]}]}))
    monkeypatch.setenv("COBOL_ACCESS_FILE", str(grants))
    seen = {i["rule_id"].split("-R")[0] for i in client.get("/v1/rules", params={"page_size": 200},
                                                              headers=H["viewer"]).json()["items"]}
    assert seen == {"FLOWPRG"}
    assert client.get("/v1/rules/TXNCHK-R001", headers=H["viewer"]).status_code == 404     # existence not leaked
    assert client.get("/v1/rules/TXNCHK-R001", headers=H["admin"]).status_code == 200      # admins see all
    progs = client.get("/v1/programs", headers=H["viewer"]).json()["items"]
    assert [p["name"] for p in progs] == ["FLOWPRG"]


def test_rate_limit_metrics_and_security_headers(client, monkeypatch):
    monkeypatch.setenv("COBOL_RATE_LIMIT_PER_MIN", "3")
    monkeypatch.setattr(main, "limiter", main.RateLimiter())
    codes = [client.get("/v1/programs", headers=H["viewer"]).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    r = client.get("/v1/programs", headers=H["viewer"])
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert client.get("/v1/programs", headers=H["admin"]).status_code == 200      # limits are per caller
    h = client.get("/v1/health").headers
    assert h["X-Content-Type-Options"] == "nosniff" and "frame-ancestors 'none'" in h["Content-Security-Policy"]
    m = client.get("/metrics").text
    assert 'cobol_http_requests_total{method="GET",route="/v1/programs",status="429"}' in m
    assert "cobol_rate_limited_total" in m and "cobol_programs " in m
    monkeypatch.setenv("COBOL_METRICS_TOKEN", "s3cret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_backup_is_verified_and_rotated_and_retention_purges(client, tmp_path, monkeypatch):
    monkeypatch.setenv("COBOL_BACKUP_DIR", str(tmp_path / "backups"))
    pid = upload_and_extract(client, TXNCHK)
    client.patch("/v1/rules/TXNCHK-R001", json={"action": "approve", "reason": "ok"}, headers=H["reviewer"])
    assert client.post("/v1/admin/backup", headers=H["reviewer"]).status_code == 403
    for _ in range(3):
        r = client.post("/v1/admin/backup", params={"keep": 2}, headers=H["admin"]).json()
        assert r["ok"] and r["integrity"] == "ok" and r["audit_chain_ok"]
        time.sleep(1.1)
    assert len(client.get("/v1/admin/backups", headers=H["admin"]).json()["items"]) == 2
    from src.api.store import Store
    copy = Store(tmp_path / "backups" / client.get("/v1/admin/backups", headers=H["admin"]).json()["items"][-1]["name"])
    assert copy.get_rule("TXNCHK-R001")[0].status == "approved"
    copy.close()
    main.store.db.execute("UPDATE jobs SET updated_at='2000-01-01T00:00:00+00:00'")
    main.store.db.commit()
    assert client.post("/v1/admin/retention", headers=H["admin"]).json()["jobs"] >= 1
