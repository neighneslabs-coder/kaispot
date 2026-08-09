"""Users, company settings, the voucher pattern and backups."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from backend import auth, db
from backend.db import execute, one, rows
from backend.services import agents as agent_service
from backend.services import maintenance

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


@bp.route("/reset", methods=["GET", "POST"])
@auth.admin_only
def reset():
    """Empty the app and start again."""
    if request.method == "POST":
        try:
            result = maintenance.reset(
                keep_packages=bool(request.form.get("keep_packages")),
                keep_buildings=bool(request.form.get("keep_buildings")),
                keep_users=bool(request.form.get("keep_users")),
                confirm=request.form.get("confirm", ""),
                user_id=auth.current_user()["id"])
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("settings.reset"))
        removed = sum(v for k, v in result["before"].items()
                      if k not in result["kept"])
        flash(f"The app was emptied. {removed:,} record(s) removed, kept: "
              f"{', '.join(result['kept'])}. A backup was saved first as "
              f"backups/{result['backup']} - restore it by copying it over "
              f"data/kaispot.db with the server stopped.", "warn")
        return redirect(url_for("settings.index"))
    return render_template("pages/reset.html",
                           contents=maintenance.what_is_there())


@bp.route("/backup", methods=["POST"])
@auth.admin_only
def backup():
    name = db.backup()
    auth.audit("backup", name)
    flash(f"Backup written to backups/{name}", "ok")
    return redirect(url_for("settings.index"))