"""Daily operations: the table the office works from."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from backend import auth
from backend.money import parse_date, parse_int, shift, today
from backend.services import agents as agent_service
from backend.services import daily as daily_service
from backend.services import ledger
from backend.services import packages as package_service
from backend.services import sales, stock

bp = Blueprint("daily", __name__, url_prefix="/daily")


def _cash_from_form(form) -> dict:
    """Pull the typed cash out of the form.

    Boxes are named cash_<package>_<price>, because an agent can be carrying
    two runs of the same package at different prices and each has to balance
    against its own price.
    """
    cash: dict = {}
    for key, raw in form.items():
        if not key.startswith("cash_"):
            continue
        parts = key.split("_")
        if len(parts) != 3:
            continue
        package_id, price = parse_int(parts[1]), parse_int(parts[2])
        if not package_id:
            continue
        if str(raw).strip() == "":
            continue
        cash[(package_id, price)] = parse_int(raw)
    return cash


@bp.route("/", methods=["GET", "POST"])
@auth.staff_only
def board():
    day = parse_date(request.values.get("day"), today())

    if request.method == "POST":
        saved = 0
        for key, raw in request.form.items():
            # One total box per agent on this screen; the split by package is
            # done on her own page.
            if not key.startswith("total_") or str(raw).strip() == "":
                continue
            agent_id = parse_int(key[len("total_"):])
            if not agent_id:
                continue
            amount = parse_int(raw)
            lines = daily_service.lines_for(agent_id, day)
            if not lines:
                continue
            if len(lines) == 1:
                cash = {(lines[0]["package_id"], lines[0]["price"]): amount}
            else:
                # Several packages and only one figure typed: put it against
                # the line with the most sold, and say so, rather than
                # guessing a split that would be wrong.
                busiest = max(lines, key=lambda l: l["sold_qty"])
                cash = {(busiest["package_id"], busiest["price"]): amount}
            try:
                daily_service.save_row(agent_id, day, cash)
                saved += 1
            except ValueError:
                continue
        flash(f"Collections saved for {saved} agent(s)." if saved
              else "Nothing was entered, so nothing changed.",
              "ok" if saved else "warn")
        return redirect(url_for("daily.board", day=day))

    return render_template(
        "pages/daily.html",
        day=day,
        yesterday=shift(day, -1),
        tomorrow=shift(day, 1),
        board=daily_service.board(day),
        totals=daily_service.day_totals(day),
        outstanding=daily_service.open_days_before(day),
        packages=package_service.all_packages(),
        agents=agent_service.all_agents(),
        office=stock.office_stock(),
    )


@bp.route("/<int:agent_id>", methods=["GET", "POST"])
@auth.staff_only
def row(agent_id: int):
    day = parse_date(request.values.get("day"), today())
    agent = agent_service.get(agent_id)
    if agent is None:
        flash("That agent does not exist.", "error")
        return redirect(url_for("daily.board"))

    if request.method == "POST":
        action = request.form.get("action", "save")
        try:
            if action == "reopen":
                daily_service.reopen_row(agent_id, day,
                                         auth.current_user()["id"])
                auth.audit("day-reopened", f"{agent['code']} {day}")
                flash("Day reopened. Everything it posted was reversed.", "warn")
                return redirect(url_for("daily.row", agent_id=agent_id, day=day))

            saved = daily_service.save_row(
                agent_id, day, _cash_from_form(request.form),
                request.form.get("note", ""))

            if action == "close":
                daily_service.close_row(agent_id, day,
                                        auth.current_user()["id"])
                auth.audit("day-closed", f"{agent['code']} {day}")
                if saved["shortage"] > 0:
                    flash(f"Day closed. {saved['shortage']:,} short, put on "
                          f"{agent['name']}'s account.", "warn")
                elif saved["surplus"] > 0:
                    flash(f"Day closed. {saved['surplus']:,} more than her "
                          f"sales account for - check the figures.", "warn")
                else:
                    flash(f"Day closed and balanced. Commission of "
                          f"{saved['commission']:,} earned.", "ok")
                return redirect(url_for("daily.board", day=day))
            flash("Saved. Check the figures, then close the day.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("daily.row", agent_id=agent_id, day=day))

    return render_template(
        "pages/daily_row.html",
        agent=agent, day=day,
        row=daily_service.get_row(agent_id, day),
        position=ledger.position(agent_id),
        sales=sales.recent_sales(agent_id, 10),
        packages=package_service.all_packages(),
        office=stock.office_stock(),
    )


@bp.route("/assign", methods=["POST"])
@auth.staff_only
def assign():
    day = parse_date(request.form.get("day"), today())
    try:
        result = stock.assign(
            agent_id=parse_int(request.form.get("agent_id")),
            package_id=parse_int(request.form.get("package_id")),
            qty=parse_int(request.form.get("qty")),
            assigned_on=day,
            note=request.form.get("note", ""),
            user_id=auth.current_user()["id"])
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        auth.audit("stock-assigned",
                   f"{result['qty']} {result['package']['name']} to "
                   f"{result['agent']['code']}")
        flash(f"{result['qty']:,} {result['package']['name']} vouchers "
              f"({result['value']:,}) given to {result['agent']['name']}.", "ok")
    return redirect(request.form.get("back") or url_for("daily.board", day=day))


@bp.route("/recall", methods=["POST"])
@auth.staff_only
def recall():
    day = parse_date(request.form.get("day"), today())
    try:
        result = stock.recall(
            agent_id=parse_int(request.form.get("agent_id")),
            package_id=parse_int(request.form.get("package_id")),
            qty=parse_int(request.form.get("qty")),
            assigned_on=day,
            user_id=auth.current_user()["id"])
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash(f"{result['qty']:,} vouchers taken back into the office.", "ok")
    return redirect(request.form.get("back") or url_for("daily.board", day=day))