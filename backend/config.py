"""Settings and folder locations. Everything lives inside this one folder."""

from __future__ import annotations

import os
import pathlib
import secrets

ROOT = pathlib.Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
BACKUP_DIR = ROOT / "backups"
FRONTEND_DIR = ROOT / "frontend"
TEMPLATE_DIR = FRONTEND_DIR / "templates"
STATIC_DIR = FRONTEND_DIR / "static"

DB_PATH = DATA_DIR / "kaispot.db"
SECRET_PATH = DATA_DIR / "secret.key"

APP_NAME = "KAISPOT"
APP_VERSION = "3.0"
CURRENCY = "UGX"

HOST = os.environ.get("KAISPOT_HOST", "0.0.0.0")

# Hosting services hand the port over in PORT and kill anything that does not
# listen on it. KAISPOT_PORT stays first so the office PC is unaffected.
PORT = int(os.environ.get("KAISPOT_PORT")
           or os.environ.get("PORT")
           or 8080)

# True when running on a server rather than the office PC: no browser to open,
# and the LAN address printed at startup means nothing.
HOSTED = bool(os.environ.get("PORT") and not os.environ.get("KAISPOT_PORT"))

MAX_UPLOAD_MB = 25


def secret_key() -> str:
    """Session key, made once and kept in data/ so reinstalling the code
    does not sign everybody out."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_PATH.exists():
        value = SECRET_PATH.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_hex(32)
    SECRET_PATH.write_text(value, encoding="utf-8")
    return value