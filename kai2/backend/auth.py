"""Sign-in and access control.

PBKDF2 from the standard library rather than bcrypt or argon2: those ship as
compiled wheels and are the usual reason pip fails on an office PC with no
build tools.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import os
from typing import Callable

from flask import flash, g, redirect, session, url_for

from backend.db import execute, one

ITERATIONS = 260_000
ROLES = ("admin", "manager", "agent")
ROLE_LABELS = {"admin": "Administrator", "manager": "Manager",
               "agent": "Sales agent"}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


def authenticate(username: str, password: str):
    row = one("SELECT * FROM users WHERE username = ? AND active = 1",
              (username.strip(),))
    if row is None:
        # Same cost as a real check, so a wrong username and a wrong password
        # take the same time from outside.
        hashlib.pbkdf2_hmac("sha256", password.encode(), b"decoy", ITERATIONS)
        return None
    return row if verify_password(password, row["password_hash"]) else None


def sign_in(user) -> None:
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True


def sign_out() -> None:
    session.clear()


def current_user():
    if "user" in g:
        return g.user
    user_id = session.get("user_id")
    g.user = None
    if user_id:
        g.user = one("SELECT * FROM users WHERE id = ? AND active = 1", (user_id,))
        if g.user is None:
            session.clear()
    return g.user


def set_password(user_id: int, password: str, must_change: int = 0) -> None:
    execute("UPDATE users SET password_hash = ?, must_change = ? WHERE id = ?",
            (hash_password(password), must_change, user_id))


def home_for(user) -> str:
    """Agents land on their point of sale, everyone else on the dashboard."""
    if user is not None and user["role"] == "agent":
        return url_for("pos.home")
    return url_for("dashboard.home")


def login_required(view: Callable) -> Callable:
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.sign_in"))
        return view(*args, **kwargs)
    return wrapped


def staff_only(view: Callable) -> Callable:
    """Admin or manager. Agents are pushed back to their own screen."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("auth.sign_in"))
        if user["role"] not in ("admin", "manager"):
            flash("That area is for office staff.", "error")
            return redirect(home_for(user))
        return view(*args, **kwargs)
    return wrapped


def admin_only(view: Callable) -> Callable:
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("auth.sign_in"))
        if user["role"] != "admin":
            flash("Only an administrator can do that.", "error")
            return redirect(home_for(user))
        return view(*args, **kwargs)
    return wrapped


def agent_only(view: Callable) -> Callable:
    """An agent screen. Staff may look, but must name whose screen."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("auth.sign_in"))
        if user["role"] == "agent" and not user["agent_id"]:
            flash("This sign-in is not linked to an agent record yet.", "error")
            return redirect(url_for("auth.sign_out"))
        return view(*args, **kwargs)
    return wrapped


def audit(action: str, detail: str = "") -> None:
    user = current_user()
    execute("INSERT INTO audit_log (user_id, action, detail) VALUES (?,?,?)",
            (user["id"] if user else None, action, detail))
