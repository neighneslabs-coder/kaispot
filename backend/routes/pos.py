"""The agent's point of sale.

Her whole screen: the cards she is holding laid out the way her paper sheet
is laid out, her customers, the sheets the office has sent her, and how she
is doing against target. Tapping a card marks it sold; the counts and the
cash value move as she taps.
"""

from __future__ import annotations

from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, url_for)

from backend import auth
from backend.db import setting
from backend.money import parse_int, today
from backend.services import agents as agent_service
from backend.services import clients as client_service
from backend.services import daily as daily_service
from backend.services import ledger, progress, sales, sheets

bp = Blueprint("pos", __name__, url_prefix="/sell")


def _agent_id() -> int | None:
    """Whose till this is. An agent sees her own; staff can look at one by
    naming it in the address."""
    user = auth.current_user()
    if user["role"] == "agent":
        return user["agent_id"]
    return parse_int(request.args.get("agent")
                     or request.form.get("agent_id")) or None


def _wants_json() -> bool:
    """True when the tap came from the page's own script rather than a plain
    form submission, so the screen can update without reloading."""
    return request.headers.get("X-Requested-With") == "fetch"


@bp.route("/")
@auth.agent_only
def home():
    agent_id = _agent_id()
    if not agent_id:
        flash("Choose which agent's till to open.", "warn")
        return redirect(url_for("agents.index"))
    agent = agent_service.get(agent_id)
    if agent is None:
        flash("That agent does not exist.", "error")
        return redirect(auth.home_for(auth.current_user()))

    day = today()
    return render_template(
        "pages/pos.html",
        agent=agent,
        groups=sales.sheet_for(agent_id),
        totals=sales.totals_for(agent_id, day),
        position=ledger.position(agent_id),
        periods=progress.agent_periods(agent_id, day),
        clients=client_service.for_agent(agent_id),
        sheets=sheets.visible_to(agent_id),
        row=daily_service.get_row(agent_id, day),
        findings=progress.for_agent(agent_id, agent["name"]),
        day=day,
    )


@bp.route("/mark", methods=["POST"])
@auth.agent_only
def mark():
    """Mark one card sold, invalid, or put it back."""
    agent_id = _agent_id()
    voucher_id = parse_int(request.form.get("voucher_id"))
    action = request.form.get("action", "sold")
    client_id = parse_int(request.form.get("client_id")) or None
    phone = request.form.get("phone", "")
    channel = request.form.get("channel", "counter")

    if client_id:
        client = client_service.get(client_id, agent_id)
        if client:
            phone = client["phone"]

    result = None
    try:
        if action == "sold":
            result = sales.sell_voucher(
                voucher_id, agent_id, channel=channel, client_phone=phone,
                client_id=client_id, user_id=auth.current_user()["id"])
            auth.audit("voucher-sold", f"agent {agent_id} voucher {voucher_id}")
        elif action == "invalid":
            sales.mark_invalid(voucher_id, agent_id)
            auth.audit("voucher-invalid", str(voucher_id))
        elif action == "restore":
            sales.restore_voucher(voucher_id, agent_id)
        elif action == "undo":
            sales.void_sale(voucher_id, agent_id)
            auth.audit("sale-voided", str(voucher_id))
        else:
            raise ValueError("Unknown action.")
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "error")
        return redirect(url_for("pos.home", agent=agent_id))

    if _wants_json():
        payload = {"ok": True, "totals": sales.totals_for(agent_id),
                   "action": action, "voucher_id": voucher_id}
        if result:
            payload["links"] = result["links"]
            payload["message"] = result["message"]
            payload["code"] = result["voucher"]["code"]
            payload["secret"] = result["voucher"]["secret"]
        return jsonify(payload)

    if action == "sold" and result:
        return render_template("pages/pos_sold.html",
                               agent=agent_service.get(agent_id),
                               result={**result,
                                       "remaining": sales.totals_for(agent_id)["left_qty"]},
                               phone=phone,
                               company=setting("company_name", "KAISPOT"))
    return redirect(url_for("pos.home", agent=agent_id))


@bp.route("/sell", methods=["POST"])
@auth.agent_only
def sell():
    """Sell the next card of a package, without picking one off the sheet."""
    agent_id = _agent_id()
    package_id = parse_int(request.form.get("package_id"))
    client_id = parse_int(request.form.get("client_id")) or None
    phone = request.form.get("phone", "")

    if client_id:
        client = client_service.get(client_id, agent_id)
        if client:
            phone = client["phone"]

    try:
        result = sales.sell(agent_id, package_id,
                            channel=request.form.get("channel", "counter"),
                            client_phone=phone, client_id=client_id,
                            user_id=auth.current_user()["id"])
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("pos.home", agent=agent_id))

    auth.audit("voucher-sold", f"agent {agent_id} package {package_id}")
    return render_template("pages/pos_sold.html",
                           agent=agent_service.get(agent_id), result=result,
                           phone=phone,
                           company=setting("company_name", "KAISPOT"))


@bp.route("/void/<int:voucher_id>", methods=["POST"])
@auth.agent_only
def void(voucher_id: int):
    agent_id = _agent_id()
    try:
        sales.void_sale(voucher_id, agent_id)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        auth.audit("sale-voided", str(voucher_id))
        flash("That sale was undone and the card put back in your stock.",
              "warn")
    return redirect(url_for("pos.home", agent=agent_id))


@bp.route("/clients", methods=["POST"])
@auth.agent_only
def add_client():
    agent_id = _agent_id()
    try:
        client_service.create(agent_id, request.form.get("name", ""),
                              request.form.get("phone", ""),
                              request.form.get("note", ""))
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash("Customer saved.", "ok")
    return redirect(url_for("pos.home", agent=agent_id))


@bp.route("/clients/<int:client_id>/delete", methods=["POST"])
@auth.agent_only
def remove_client(client_id: int):
    agent_id = _agent_id()
    client_service.delete(client_id, agent_id)
    flash("Customer removed.", "ok")
    return redirect(url_for("pos.home", agent=agent_id))