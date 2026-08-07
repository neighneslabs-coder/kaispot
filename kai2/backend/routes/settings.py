"""Users, company settings, the voucher pattern and backups."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from backend import auth, db
from backend.db import execute, one, rows
from backend.services import agents as agent_service
from backend.services import pdfread

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.route("/")
@auth.admin_only
def index():
    return render_template(
        "pages/settings.html",
        users=rows("SELECT u.*, a.name AS agent_name FROM users u "
                   "LEFT JOIN agents a ON a.id = u.agent_id ORDER BY u.username"),
        agents=agent_service.all_agents(),
        company=db.setting("company_name", "KAISPOT"),
        pattern=db.setting("voucher_pattern", pdfread.DEFAULT_PATTERN),
        country=db.setting("country_code", "256"),
        roles=auth.ROLES,
        audit=rows("SELECT a.*, u.username FROM audit_log a "
                   "LEFT JOIN users u ON u.id = a.user_id "
                   "ORDER BY a.id DESC LIMIT 50"))


@bp.route("/company", methods=["POST"])
@auth.admin_only
def company():
    for key in ("company_name", "country_code"):
        value = request.form.get(key, "").strip()
        if value:
            db.set_setting(key, value)
    pattern = request.form.get("voucher_pattern", "").strip()
    if pattern:
        try:
            pdfread.find_codes("test123", pattern)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("settings.index"))
        db.set_setting("voucher_pattern", pattern)
    flash("Settings saved.", "ok")
    return redirect(url_for("settings.index"))


@bp.route("/users/new", methods=["POST"])
@auth.admin_only
def new_user():
    username = request.form.get("username", "").strip().lower()
    role = request.form.get("role", "agent")
    agent_id = request.form.get("agent_id") or None
    if not username:
        flash("Give the account a username.", "error")
    elif role not in auth.ROLES:
        flash("Choose a valid role.", "error")
    elif role == "agent" and not agent_id:
        flash("An agent account must be linked to an agent record.", "error")
    elif one("SELECT id FROM users WHERE username = ?", (username,)):
        flash(f"The username {username} is already taken.", "error")
    else:
        execute("INSERT INTO users (username, password_hash, full_name, role, "
                "agent_id, must_change) VALUES (?,?,?,?,?,1)",
                (username, auth.hash_password("changeme"),
                 request.form.get("full_name", "").strip(), role,
                 int(agent_id) if agent_id else None))
        auth.audit("user-created", username)
        flash("Account created with the password changeme. They must change it "
              "the first time they sign in.", "ok")
    return redirect(url_for("settings.index"))


@bp.route("/users/<int:user_id>/reset", methods=["POST"])
@auth.admin_only
def reset_password(user_id: int):
    user = one("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        flash("That account does not exist.", "error")
    else:
        auth.set_password(user_id, "changeme", must_change=1)
        auth.audit("password-reset", user["username"])
        flash(f"Password for {user['username']} reset to changeme.", "ok")
    return redirect(url_for("settings.index"))


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@auth.admin_only
def toggle_user(user_id: int):
    me = auth.current_user()
    if user_id == me["id"]:
        flash("You cannot switch off your own account.", "error")
        return redirect(url_for("settings.index"))
    user = one("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is not None:
        execute("UPDATE users SET active = ? WHERE id = ?",
                (0 if user["active"] else 1, user_id))
        auth.audit("user-toggled", user["username"])
        flash(f"{user['username']} is now "
              f"{'switched off' if user['active'] else 'active'}.", "ok")
    return redirect(url_for("settings.index"))


@bp.route("/backup", methods=["POST"])
@auth.admin_only
def backup():
    name = db.backup()
    auth.audit("backup", name)
    flash(f"Backup written to backups/{name}", "ok")
    return redirect(url_for("settings.index"))
