"""SQLite access. One connection per request, WAL so a reader never blocks
an agent trying to record a sale."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from flask import g

from backend.config import BACKUP_DIR, DATA_DIR, DB_PATH, ROOT, UPLOAD_DIR


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc: object = None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def query(sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    return get_db().execute(sql, params).fetchall()


def rows(sql: str, params: tuple | dict = ()) -> list[dict]:
    return [dict(r) for r in query(sql, params)]


def one(sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
    return get_db().execute(sql, params).fetchone()


def one_dict(sql: str, params: tuple | dict = ()) -> dict | None:
    row = one(sql, params)
    return dict(row) if row else None


def scalar(sql: str, params: tuple | dict = (), default: int = 0) -> int:
    row = get_db().execute(sql, params).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]


def execute(sql: str, params: tuple | dict = ()) -> int:
    return int(get_db().execute(sql, params).lastrowid or 0)


class transaction:
    """Wrap a set of writes so they all land or none do. Closing a day touches
    four tables; half of it applied would leave an agent's balance wrong."""

    def __enter__(self) -> sqlite3.Connection:
        self.conn = get_db()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
        return False


def init_db() -> None:
    """Create the schema if absent. Safe on every start."""
    for folder in (DATA_DIR, UPLOAD_DIR, BACKUP_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    sql = (ROOT / "backend" / "schema.sql").read_text(encoding="utf-8")
    conn = connect()
    try:
        conn.executescript(sql)
    finally:
        conn.close()


def backup() -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"kaispot-{stamp}.db"
    source = connect()
    try:
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return target.name


def setting(key: str, default: str = "") -> str:
    row = one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute("INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)))
