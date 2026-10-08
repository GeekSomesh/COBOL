"""Bearer-token auth with roles viewer < reviewer < admin (security.md 4.2).

Tokens come from ``COBOL_API_TOKENS`` ("token:role:user,token:role:user"). Without
it, development tokens are generated once into data/dev_tokens.json (git-ignored)
and printed at startup. Secrets never live in code.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

ROLES = {"viewer": 1, "reviewer": 2, "admin": 3}


@dataclass(frozen=True)
class User:
    name: str
    role: str

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
