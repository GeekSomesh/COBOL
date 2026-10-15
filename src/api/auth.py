"""Authentication: bearer tokens and OpenID Connect, roles viewer < reviewer < admin (security.md 4.2).

Static tokens come from ``COBOL_API_TOKENS`` ("token:role:user,token:role:user"),
for service accounts and development. Without it, development tokens are generated
once into data/dev_tokens.json (git-ignored). Secrets never live in code.

OpenID Connect (single sign-on) is on when ``COBOL_OIDC_ISSUER`` is set. The API then
also accepts JWT access tokens from that issuer:

  COBOL_OIDC_ISSUER        issuer URL (its /.well-known/openid-configuration names the keys)
  COBOL_OIDC_AUDIENCE      expected ``aud`` (this API's client id)
  COBOL_OIDC_JWKS_URL      key set URL (optional; discovered from the issuer otherwise)
  COBOL_OIDC_ROLE_CLAIM    claim holding roles or groups (default "roles")
  COBOL_OIDC_ROLE_MAP      "admin=cobol-admin,reviewer=cobol-reviewer,viewer=cobol-viewer"
  COBOL_OIDC_GROUPS_CLAIM  claim with the user's groups, for per-program access (default "groups")

Signature (RS256/ES256 only), issuer, audience and expiry are checked; a token whose
roles map to nothing gets no access.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ROLES = {"viewer": 1, "reviewer": 2, "admin": 3}


@dataclass(frozen=True)
class User:
    name: str
    role: str
    groups: tuple[str, ...] = field(default=())

    def can(self, role: str) -> bool:
        return ROLES[self.role] >= ROLES[role]


def load_tokens(dev_file: Path) -> dict[str, User]:
    raw = os.environ.get("COBOL_API_TOKENS", "").strip()
    tokens: dict[str, User] = {}
    if raw:
        for item in raw.split(","):
            tok, role, *user = item.strip().split(":")
            if role not in ROLES:
                raise ValueError(f"unknown role {role!r} in COBOL_API_TOKENS")
            tokens[tok] = User(user[0] if user else role, role)
        return tokens
    if dev_file.exists():
        data = json.loads(dev_file.read_text(encoding="utf-8"))
    else:
        data = {role: "dev-" + role + "-" + secrets.token_urlsafe(12) for role in ROLES}
        dev_file.parent.mkdir(parents=True, exist_ok=True)
        dev_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {tok: User(f"dev-{role}", role) for role, tok in data.items()}


class InvalidToken(Exception):
    pass


class OIDC:
    """Validates JWT access tokens from one OpenID Connect issuer."""

    def __init__(self, env: Optional[dict[str, str]] = None, jwks: Optional[Any] = None):
        e = env if env is not None else os.environ
        self.issuer = (e.get("COBOL_OIDC_ISSUER") or "").rstrip("/")
        self.audience = e.get("COBOL_OIDC_AUDIENCE") or None
        self.jwks_url = e.get("COBOL_OIDC_JWKS_URL") or None
        self.role_claim = e.get("COBOL_OIDC_ROLE_CLAIM", "roles")
        self.groups_claim = e.get("COBOL_OIDC_GROUPS_CLAIM", "groups")
        mapping = e.get("COBOL_OIDC_ROLE_MAP", "admin=cobol-admin,reviewer=cobol-reviewer,viewer=cobol-viewer")
        self.role_map = {}
        for part in mapping.split(","):
            role, _, value = part.partition("=")
            if role.strip() in ROLES and value.strip():
                self.role_map[value.strip()] = role.strip()
        self._jwks = jwks                         # a PyJWKClient-like object (tests inject one)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.issuer)

    def _client(self) -> Any:
        with self._lock:
            if self._jwks is None:
                import jwt
                import requests
                url = self.jwks_url
                if not url:
                    conf = requests.get(self.issuer + "/.well-known/openid-configuration", timeout=5).json()
                    url = conf["jwks_uri"]
                self._jwks = jwt.PyJWKClient(url, cache_keys=True, lifespan=600)
            return self._jwks

    def user(self, token: str) -> User:
        import jwt
        try:
            key = self._client().get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=self.audience,
                                issuer=self.issuer, options={"require": ["exp", "iss"], "verify_aud": bool(self.audience)},
                                leeway=30)
        except Exception as exc:                  # signature, expiry, issuer, audience, unknown key
            raise InvalidToken(str(exc)) from exc

        def values(name: str) -> list[str]:
            v = claims.get(name)
            if isinstance(v, str):
                return v.split()
            return [str(x) for x in v] if isinstance(v, list) else []

        granted = [self.role_map[v] for v in values(self.role_claim) + values(self.groups_claim) if v in self.role_map]
        if not granted:
            raise InvalidToken("token carries no role for this API")
        role = max(granted, key=lambda r: ROLES[r])
        name = claims.get("preferred_username") or claims.get("email") or claims.get("sub") or "oidc-user"
        return User(str(name), role, tuple(values(self.groups_claim)))


_token_cache: dict[str, tuple[float, User]] = {}


def oidc_user(oidc: OIDC, token: str) -> User:
    """Validated user for a JWT, cached briefly (signature checks are not free)."""
    hit = _token_cache.get(token)
    now = time.time()
    if hit and hit[0] > now:
        return hit[1]
    user = oidc.user(token)
    if len(_token_cache) > 5000:
        _token_cache.clear()
    _token_cache[token] = (now + 60, user)
    return user
