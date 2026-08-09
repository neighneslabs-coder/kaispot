"""Sales agents: the list, the profile, payments and targets."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from backend import auth
from backend.db import execute, one
from backend.money import parse_date, parse_int, today
from backend.services import agents as agent_service
from backend.services import clients as client_service
from backend.services import daily as daily_service
from backend.services import buildings as building_service
from backend.services import maintenance
from backend.services import ledger, packages as package_service
from backend.services import progress, sales, sheets, stock

bp = Blueprint("agents", __name__, url_prefix="/agents")


@bp.route("/")
@auth.staff_only
def index():
    return render_template("pages/agents.html",
                           agents=agent_service.all_agents(include_inactive=True),
                           positions={p["id"]: p for p in
                                      ledger.all_positions(include_inactive=True)},
                           company=ledger.company_totals())


@bp.route("/new", methods=["GET", "POST"])
@auth.staff_only
def new():
    if request.method == "POST":
        try:
            agent_id = agent_service.create(
                code=request.form.get("code", ""),
                name=request.form.get("name", ""),
                phone=request.form.get("phone", ""),
                station=request.form.get("station", ""),
                commission_rate=parse_int(request.form.get("commission_rate"), 1000),
                monthly_target=parse_int(request.form.get("monthly_target")),
                daily_target=parse_int(request.form.get("daily_target")),
                credit_limit=parse_int(request.form.get("credit_limit")),
                nin=request.form.get("nin", ""),
                next_of_kin=request.form.get("next_of_kin", ""),
                notes=request.form.get("notes", ""),
                building_id=parse_int(request.form.get("building_id")) or None)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("pages/agent_form.html", agent=request.form,
                                   buildings=building_service.all_buildings(),
                                   heading="Add a sales agent")
        auth.audit("agent-created", request.form.get("code", ""))
        flash("Agent added. Give them a sign-in so they can use the point of "
              "sale.", "ok")
        return redirect(url_for("agents.profile", agent_id=agent_id))
    return render_template("pages/agent_form.html",
                           agent={"commission_rate": 1000, "active": 1},
                           buildings=building_service.all_buildings(),
                           heading="Add a sales agent")


@bp.route("/<int:agent_id>/edit", methods=["GET", "POST"])
@auth.staff_only
def edit(agent_id: int):
    agent = agent_service.get(agent_id)
    if agent is None:
        flash("That agent does not exist.", "error")
        return redirect(url_for("agents.index"))
    if request.method == "POST":
        try:
            agent_service.update(
                agent_id,
                name=request.form.get("name", ""),
                phone=request.form.get("phone", ""),
                station=request.form.get("station", ""),
                commission_rate=parse_int(request.form.get("commission_rate"), 1000),
                monthly_target=parse_int(request.form.get("monthly_target")),
                daily_target=parse_int(request.form.get("daily_target")),
                credit_limit=parse_int(request.form.get("credit_limit")),
                nin=request.form.get("nin", ""),
                next_of_kin=request.form.get("next_of_kin", ""),
                notes=request.form.get("notes", ""),
                active=1 if request.form.get("active") else 0,
                building_id=parse_int(request.form.get("building_id")) or None)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("pages/agent_form.html", agent=request.form,
                                   buildings=building_service.all_buildings(),
                                   heading=f"Edit {agent['name']}")
        auth.audit("agent-updated", agent["code"])
        flash("Agent updated.", "ok")
        return redirect(url_for("agents.profile", agent_id=agent_id))
    return render_template("pages/agent_form.html", agent=agent,
                           buildings=building_service.all_buildings(),
                           heading=f"Edit {agent['name']}")


@bp.route("/<int:agent_id>/delete", methods=["POST"])
@auth.staff_only
def delete(agent_id: int):
    """Switch an agent off, or delete them one of two ways."""
    mode = request.form.get("mode", "deactivate")
    if mode != "deactivate" and auth.current_user()["role"] != "admin":
        flash("Only an administrator can delete an agent's records.", "error")
        return redirect(url_for("agents.profile", agent_id=agent_id))
    try:
        result = maintenance.delete_agent(
            agent_id, mode, request.form.get("confirm", ""),
            auth.current_user()["id"])
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("agents.profile", agent_id=agent_id))

    agent, foot = result["agent"], result["footprint"]
    if mode == "deactivate":
        flash(f"{agent['name']} was switched off. Every figure they produced "
              f"stays, and they can be switched back on at any time.", "ok")
        return redirect(url_for("agents.profile", agent_id=agent_id))
    if mode == "agent_only":
        flash(f"{agent['name']} was deleted. {foot['holding']:,} unsold "
              f"voucher(s) went back to the office shelf and {foot['sold']:,} "
              f"sold one(s) stay on record. Backup saved as "
              f"backups/{result['backup']}.", "warn")
    else:
        flash(f"{agent['name']} and everything of theirs was deleted, "
              f"including {foot['sold']:,} sold voucher(s). Backup saved as "
              f"backups/{result['backup']}.", "warn")
    return redirect(url_for("agents.index"))


@bp.route("/<int:agent_id>")
@auth.login_required
def profile(agent_id: int):
    user = auth.current_user()
    if user["role"] == "agent" and user["agent_id"] != agent_id:
        flash("You can only open your own record.", "error")
        return redirect(auth.home_for(user))
    agent = agent_service.get(agent_id)
    if agent is None:
        flash("That agent does not exist.", "error")
        return redirect(url_for("agents.index"))
    return render_template(
        "pages/agent_profile.html",
        agent=agent,
        position=ledger.position(agent_id),
        periods=progress.agent_periods(agent_id),
        stock=agent_service.stock_by_package(agent_id),
        stock_value=agent_service.stock_value(agent_id),
        entries=ledger.entries(agent_id, 60),
        labels=ledger.LABELS,
        history=daily_service.history(agent_id, 30),
        sales=sales.recent_sales(agent_id, 15),
        sheets=sheets.visible_to(agent_id),
        clients=client_service.for_agent(agent_id),
        assignments=stock.assignments(agent_id, 20),
        login=one("SELECT * FROM users WHERE agent_id = ?", (agent_id,)),
        footprint=maintenance.agent_footprint(agent_id),
        packages=package_service.all_packages(),
        today=today(),
    )


@bp.route("/<int:agent_id>/pay", methods=["POST"])
@auth.staff_only
def pay(agent_id: int):
    """Record a debt payment. Every balance in the system is a sum over the
    ledger, so this one entry moves them all at once."""
    try:
        result = ledger.record_payment(
            agent_id,
            parse_date(request.form.get("entry_date"), today()),
            parse_int(request.form.get("amount")),
            request.form.get("note", ""),
            auth.current_user()["id"])
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        auth.audit("payment", f"agent {agent_id}")
        message = f"Payment of {result['paid']:,} recorded."
        if result["commission"]:
            message += f" Commission of {result['commission']:,} earned on it."
        message += f" Debt is now {result['position']['debt']:,}."
        flash(message, "ok")
    return redirect(request.form.get("back")
                    or url_for("agents.profile", agent_id=agent_id))


@bp.route("/<int:agent_id>/commission", methods=["POST"])
@auth.staff_only
def commission(agent_id: int):
    try:
        ledger.pay_commission(
            agent_id, parse_date(request.form.get("entry_date"), today()),
            parse_int(request.form.get("amount")),
            request.form.get("note", ""), auth.current_user()["id"])
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        auth.audit("commission-paid", f"agent {agent_id}")
        flash("Commission payout recorded.", "ok")
    return redirect(request.form.get("back")
                    or url_for("agents.profile", agent_id=agent_id))


@bp.route("/<int:agent_id>/adjust", methods=["POST"])
@auth.admin_only
def adjust(agent_id: int):
    kind = request.form.get("kind", "adjustment")
    try:
        if kind == "writeoff":
            ledger.write_off(agent_id,
                             parse_date(request.form.get("entry_date"), today()),
                             parse_int(request.form.get("amount")),
                             request.form.get("note", ""),
                             auth.current_user()["id"])
        else:
            ledger.adjust(agent_id,
                          parse_date(request.form.get("entry_date"), today()),
                          parse_int(request.form.get("amount")),
                          request.form.get("note", ""),
                          auth.current_user()["id"])
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        auth.audit("ledger-adjusted", f"agent {agent_id} {kind}")
        flash("Posted to the account.", "ok")
    return redirect(url_for("agents.profile", agent_id=agent_id))


@bp.route("/<int:agent_id>/login", methods=["POST"])
@auth.staff_only
def create_login(agent_id: int):
    agent = agent_service.get(agent_id)
    if agent is None:
        flash("That agent does not exist.", "error")
        return redirect(url_for("agents.index"))
    if one("SELECT id FROM users WHERE agent_id = ?", (agent_id,)):
        flash("That agent already has a sign-in.", "warn")
        return redirect(url_for("agents.profile", agent_id=agent_id))
    username = agent["code"].lower().replace(" ", "")
    if one("SELECT id FROM users WHERE username = ?", (username,)):
        flash(f"The username {username} is taken. Change the agent code first.",
              "error")
        return redirect(url_for("agents.profile", agent_id=agent_id))
    execute("INSERT INTO users (username, password_hash, full_name, role, "
            "agent_id, must_change) VALUES (?,?,?,'agent',?,1)",
            (username, auth.hash_password("changeme"), agent["name"], agent_id))
    auth.audit("agent-login-created", username)
    flash(f"Sign-in created. Username {username}, password changeme. They must "
          f"change it the first time they sign in.", "ok")
    return redirect(url_for("agents.profile", agent_id=agent_id))


@bp.route("/<int:agent_id>/sheet", methods=["POST"])
@auth.staff_only
def upload_sheet(agent_id: int):
    """Kept only so an old bookmark does not break.

    Issuing a sheet happens in one place now. This route used to store the
    file without registering its cards, so the agent ended up looking at a
    sheet she could not sell from.
    """
    return redirect(url_for("stock.sheet_list"))