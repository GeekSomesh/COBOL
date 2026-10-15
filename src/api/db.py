"""Database connections for the rule store: SQLite (default) or PostgreSQL.

``COBOL_DB`` is either a path to an SQLite file (default ``data/rules.db``) or a
``postgresql://user:password@host:port/database`` URL (driver: psycopg 3). The
store writes portable SQL with ``?`` parameters; for PostgreSQL this module
rewrites the few SQLite-specific parts:

* ``?`` -> ``%s``; ``INTEGER PRIMARY KEY AUTOINCREMENT`` -> ``BIGSERIAL PRIMARY KEY``
* the column name ``user`` (reserved in PostgreSQL) is quoted
* ``INSERT OR REPLACE`` -> ``INSERT ... ON CONFLICT (key) DO UPDATE``
* ``PRAGMA table_info(t)`` -> ``information_schema.columns``
* inserts into tables with generated ids return the id (``lastrowid``)

Rows behave like ``sqlite3.Row`` (``row["col"]``, ``row[0]``, ``dict(row)``).
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


def is_url(target: str) -> bool:
    return str(target).startswith(("postgresql://", "postgres://"))


def connect(target: str) -> Any:
    if is_url(target):
        return PGConnection(target)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


class Row(dict):
    """dict row that also answers ``row[0]`` like sqlite3.Row."""

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


_UPSERT = {"assignments": ("rule_id", ["assignee", "assigned_by", "ts"])}
_RETURNING = {"comments", "notifications", "reviews"}
_USER = re.compile(r'(?<![\w"])user(?![\w"])')


def translate(sql: str) -> str:
    s = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    m = re.match(r"\s*PRAGMA table_info\((\w+)\)", s)
    if m:
        return ("SELECT column_name AS name FROM information_schema.columns "
                f"WHERE table_schema = current_schema() AND table_name = '{m.group(1)}'")
    m = re.match(r"\s*INSERT OR REPLACE INTO (\w+)", s)
    if m:
        table = m.group(1)
        key, cols = _UPSERT[table]
        s = s.replace("INSERT OR REPLACE INTO", "INSERT INTO", 1)
        s += f" ON CONFLICT ({key}) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    s = re.sub(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", r"ALTER TABLE \1 ADD COLUMN IF NOT EXISTS \2", s)
    s = _USER.sub('"user"', s)
    return s.replace("?", "%s")


class PGCursor:
    def __init__(self, cur: Any, lastrowid: Optional[int] = None):
        self._cur = cur
        self.lastrowid = lastrowid
        self.rowcount = cur.rowcount

    def fetchone(self) -> Optional[Row]:
        if self._cur.description is None:
            return None
        return self._cur.fetchone()

    def fetchall(self) -> list[Row]:
        if self._cur.description is None:
            return []
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self.fetchall())


class PGConnection:
    """The slice of the sqlite3 connection API the store uses, on PostgreSQL."""

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row

        def rows(cursor):
            make = dict_row(cursor)
            return lambda values: Row(make(values))

        self.conn = psycopg.connect(url, autocommit=False, row_factory=rows)
        self._lock = threading.RLock()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> PGCursor:
        q = translate(sql)
        table = re.match(r"\s*INSERT INTO (\w+)", sql)
        returning = table is not None and table.group(1) in _RETURNING
        if returning:
            q += " RETURNING id"
        with self._lock:
            try:
                cur = self.conn.execute(q, tuple(params))
            except Exception:
                self.conn.rollback()
                raise
            last = None
            if returning:
                row = cur.fetchone()
                last = row["id"] if row else None
            return PGCursor(cur, last)

    def executescript(self, script: str) -> None:
        for stmt in [s.strip() for s in script.split(";") if s.strip()]:
            self.execute(stmt)

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self.conn.rollback()

    def close(self) -> None:
        self.conn.close()


def pg_dump(url: str, dest: Path) -> None:
    """Backup of a PostgreSQL store with pg_dump (custom format); pg_dump must be on PATH."""
    import shutil
    import subprocess
    if shutil.which("pg_dump") is None:
        raise RuntimeError("pg_dump is not installed; back up PostgreSQL with your database tooling")
    subprocess.run(["pg_dump", "--format=custom", "--file", str(dest), url], check=True, timeout=3600)
