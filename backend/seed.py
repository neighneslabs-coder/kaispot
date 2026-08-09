"""First-run setup. Runs on every start but only acts when the database is
empty, so it is safe."""

from __future__ import annotations

from backend.auth import hash_password
from backend.db import connect, init_db

DEFAULT_ADMIN = "admin"
DEFAULT_PASSWORD = "admin"

DEFAULT_SETTINGS = {
    "company_name": "KAISPOT",
    "currency": "UGX",
    "country_code": "256",
    "voucher_pattern": r"\b(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{3,15}\b",
}

STARTER_PACKAGES = [
    ("Daily", 2_000, "24 hours", "DAY", "#0b6e4f", 1),
    ("3 Days", 5_000, "72 hours", "D3", "#1d4ed8", 2),
    ("Weekly", 10_000, "7 days", "WK", "#b26b00", 3),
    ("Monthly", 35_000, "30 days", "MTH", "#7c3aed", 4),
]


def bootstrap() -> bool:
    """Returns True when it created the first administrator."""
    init_db()
    conn = connect()
    try:
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                         "ON CONFLICT(key) DO NOTHING", (key, value))
        if not conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]:
            conn.executemany(
                "INSERT INTO packages (name, price, validity, prefix, colour, "
                "sort_order) VALUES (?,?,?,?,?,?)", STARTER_PACKAGES)
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return False
        conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role, "
            "must_change) VALUES (?,?,?,'admin',1)",
            (DEFAULT_ADMIN, hash_password(DEFAULT_PASSWORD), "Administrator"))
        return True
    finally:
        conn.close()


def demo_data() -> None:
    """A few agents to try the system with."""
    init_db()
    conn = connect()
    try:
        if conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]:
            return
        conn.executemany(
            "INSERT INTO agents (code, name, phone, station, commission_rate, "
            "monthly_target, daily_target) VALUES (?,?,?,?,?,?,?)",
            [("A-01", "Namono Grace", "0771000001", "Kireka", 1000, 3_000_000, 120_000),
             ("A-02", "Okello Brian", "0771000002", "Bweyogerere", 1000, 2_500_000, 100_000),
             ("A-03", "Nakato Sarah", "0771000003", "Ntinda", 1200, 2_000_000, 80_000)])
    finally:
        conn.close()
