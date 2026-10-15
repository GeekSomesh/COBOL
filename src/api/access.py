"""Per-program access control.

Without an access file every signed-in user sees every program (roles still decide
what they may do). With ``data/access.json`` (or the file named by
``COBOL_ACCESS_FILE``) non-admin users only see the programs granted to them or to
one of their groups (OIDC groups claim)::

    {"grants": [
        {"user": "rita", "programs": ["CRD*", "TXNCHK"]},
        {"group": "loans-team", "programs": ["LON*"]},
        {"user": "auditor", "programs": ["*"]}
    ]}

Patterns match the PROGRAM-ID or the file name (without extension), case-insensitively.
Rules and programs outside a user's grants answer 404, so their existence is not leaked.
"""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Iterable, Optional

from src.api.auth import User

ROOT = Path(__file__).resolve().parents[2]
_cache: tuple[Optional[float], str, list[dict]] = (None, "", [])


def _grants() -> Optional[list[dict]]:
    global _cache
    p = Path(os.environ.get("COBOL_ACCESS_FILE") or ROOT / "data" / "access.json")
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    if _cache[0] != mtime or _cache[1] != str(p):
        _cache = (mtime, str(p), json.loads(p.read_text(encoding="utf-8")).get("grants", []))
    return _cache[2]


def patterns(user: User) -> Optional[list[str]]:
    """None: everything. Otherwise the program patterns this user may see (maybe none)."""
    grants = _grants()
    if grants is None or user.role == "admin":
        return None
    out: list[str] = []
    for g in grants:
        if g.get("user") == user.name or (g.get("group") and g["group"] in user.groups):
            out += [str(p).upper() for p in g.get("programs", [])]
    return None if "*" in out else out


def can_see(user: User, *names: Optional[str]) -> bool:
    pats = patterns(user)
    if pats is None:
        return True
    keys = {Path(n).stem.upper() for n in names if n} | {str(n).upper() for n in names if n}
    return any(fnmatch.fnmatchcase(k, p) for k in keys for p in pats)


def visible(user: User, items: Iterable, *key_funcs) -> list:
    return [x for x in items if can_see(user, *(f(x) for f in key_funcs))]
