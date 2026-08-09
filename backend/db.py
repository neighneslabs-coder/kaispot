"""SQLite access. One connection per request, WAL so a reader never blocks
an agent trying to record a sale.

This module also carries the migration step. The schema file only ever runs
CREATE TABLE IF NOT EXISTS, which cannot add a column to a table that already
exists, so an installed database would never gain new fields. `migrate()`
looks at what is actually there and adds whatever is missing, on every start.
It is safe to run repeatedly and never touches existing rows.
"""

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
    """Wrap a set of writes so they all land or none do. Balancing a day
    touches four tables; half of it applied would leave a figure wrong."""

    def __enter__(self) -> sqlite3.Connection:
        self.conn = get_db()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
        return False


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------

# Columns added since the first release, as
#     table -> (column, its full definition)
#
# Every definition must carry a DEFAULT, because SQLite refuses to add a NOT
# NULL column to a table that already has rows unless it knows what to put in
# them.
NEW_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "agents": [
        # The building or site this agent works. Nullable, because an agent
        # can be roving or newly hired before a site is assigned.
        ("building_id", "INTEGER REFERENCES buildings(id) ON DELETE SET NULL"),
    ],
    "assignments": [
        # Which batch this hand-over came from. Without it, removing a batch
        # registered straight to an agent would leave her top-up standing for
        # cards that no longer exist.
        ("intake_id", "INTEGER"),
    ],
    "vouchers": [
        # The username printed on the card. The code column stays as the
        # card's identity - PLAT-0805-014, matching the [14] on the paper -
        # because that is what the agent taps and what the office counts.
        # This is what the customer actually types to get online.
        ("username", "TEXT NOT NULL DEFAULT ''"),
    ],
    "daily_rows": [
        # The day expressed in vouchers as well as in shillings. Vouchers are
        # what the office hands over; shillings are what comes back. Both are
        # kept because a day only makes sense read both ways.
        ("opening_qty", "INTEGER NOT NULL DEFAULT 0"),
        ("topup_qty", "INTEGER NOT NULL DEFAULT 0"),
        ("sold_qty", "INTEGER NOT NULL DEFAULT 0"),
        ("closing_qty", "INTEGER NOT NULL DEFAULT 0"),
        # What the vouchers she is recorded as having sold are worth.
        ("expected", "INTEGER NOT NULL DEFAULT 0"),
        # Cash that does not add up to whole vouchers: 45,000 against a
        # 10,000 package is four vouchers sold and 5,000 left unexplained.
        ("variance", "INTEGER NOT NULL DEFAULT 0"),
    ],
}

# One row per package per agent per day. A day is balanced package by package
# because cash cannot be split between denominations on its own: 45,000
# against Daily at 2,000 and Weekly at 10,000 has no single answer.
NEW_TABLES = {
    # The sites the business sells in: blocks, hostels, arcades. One agent
    # covers one building, and a building keeps its own record even after the
    # agent covering it changes.
    "buildings": """
        CREATE TABLE IF NOT EXISTS buildings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            area          TEXT    NOT NULL DEFAULT '',
            address       TEXT    NOT NULL DEFAULT '',
            contact_name  TEXT    NOT NULL DEFAULT '',
            contact_phone TEXT    NOT NULL DEFAULT '',
            units         INTEGER NOT NULL DEFAULT 0,
            router        TEXT    NOT NULL DEFAULT '',
            notes         TEXT    NOT NULL DEFAULT '',
            active        INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "daily_lines": """
        CREATE TABLE IF NOT EXISTS daily_lines (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id    INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            day         TEXT    NOT NULL,
            package_id  INTEGER NOT NULL REFERENCES packages(id) ON DELETE RESTRICT,
            price       INTEGER NOT NULL,
            opening_qty INTEGER NOT NULL DEFAULT 0,
            topup_qty   INTEGER NOT NULL DEFAULT 0,
            cash        INTEGER NOT NULL DEFAULT 0,
            sold_qty    INTEGER NOT NULL DEFAULT 0,
            closing_qty INTEGER NOT NULL DEFAULT 0,
            expected    INTEGER NOT NULL DEFAULT 0,
            variance    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE (agent_id, day, package_id)
        )
    """,
}

NEW_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_agents_building ON agents(building_id)",
    "CREATE INDEX IF NOT EXISTS ix_daily_lines ON daily_lines(agent_id, day)",
    "CREATE INDEX IF NOT EXISTS ix_daily_lines_day ON daily_lines(day)",
]


def _columns_of(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _tables_of(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def migrate(conn: sqlite3.Connection | None = None) -> list[str]:
    """Bring an existing database up to date. Returns what it changed."""
    owned = conn is None
    conn = conn or connect()
    changed: list[str] = []
    try:
        present = _tables_of(conn)

        for table, statement in NEW_TABLES.items():
            if table not in present:
                conn.execute(statement)
                changed.append(f"created {table}")

        for table, columns in NEW_COLUMNS.items():
            if table not in present:
                continue
            existing = _columns_of(conn, table)
            for name, definition in columns:
                if name in existing:
                    continue
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                changed.append(f"{table}.{name}")

        for statement in NEW_INDEXES:
            conn.execute(statement)

        # Anything already typed into the old `station` box becomes a real
        # building, and its agents are linked to it. Done once: after this
        # the buildings table is the record and station is only a fallback.
        if ("agents" in present
                and "building_id" in _columns_of(conn, "agents")):
            stations = [r[0] for r in conn.execute(
                "SELECT DISTINCT TRIM(station) FROM agents "
                "WHERE TRIM(station) <> '' AND building_id IS NULL")]
            for name in stations:
                conn.execute("INSERT INTO buildings (name) VALUES (?) "
                             "ON CONFLICT(name) DO NOTHING", (name,))
                conn.execute(
                    "UPDATE agents SET building_id = "
                    "  (SELECT id FROM buildings WHERE name = ?) "
                    "WHERE TRIM(station) = ? AND building_id IS NULL",
                    (name, name))
            if stations:
                changed.append(f"{len(stations)} station(s) became buildings")
    finally:
        if owned:
            conn.close()
    return changed


def init_db() -> None:
    """Create the schema if absent, then migrate. Safe on every start."""
    for folder in (DATA_DIR, UPLOAD_DIR, BACKUP_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    sql = (ROOT / "backend" / "schema.sql").read_text(encoding="utf-8")
    conn = connect()
    try:
        conn.executescript(sql)
        migrate(conn)
    finally:
        conn.close()


def backup() -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"kaispot-{stamp}.db"
    # Two deletions inside the same second would otherwise share a filename,
    # and the second backup would quietly overwrite the first - losing the
    # very copy someone would want to go back to.
    suffix = 2
    while target.exists():
        target = BACKUP_DIR / f"kaispot-{stamp}-{suffix}.db"
        suffix += 1
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