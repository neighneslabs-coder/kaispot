"""Buildings: the record of every site the company sells in."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from backend import auth
from backend.money import month_bounds, parse_date, parse_int, today
from backend.services import buildings as building_service
from backend.services import progress

bp = Blueprint("buildings", __name__, url_prefix="/buildings")


def _span() -> tuple[str, str]:
    end = parse_date(request.args.get("end"), today())
    start = parse_date(request.args.get("start"), month_bounds(end)[0])
    return (end, start) if start > end else (start, end)


@bp.route("/")
@auth.staff_only
def index():
    start, end = _span()
    return render_template(
        "pages/buildings.html",
        buildings=building_service.overview(start, end),
        unposted=building_service.unposted(start, end),
        start=start, end=end)


@bp.route("/new", methods=["GET", "POST"])
@auth.staff_only
def new():
    if request.method == "POST":
        try:
            building_id = building_service.create(
                name=request.form.get("name", ""),
                area=request.form.get("area", ""),
                address=request.form.get("address", ""),
                contact_name=request.form.get("contact_name", ""),
                contact_phone=request.form.get("contact_phone", ""),
                units=parse_int(request.form.get("units")),
                router=request.form.get("router", ""),
                notes=request.form.get("notes", ""))
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("pages/building_form.html",
                                   building=request.form,
                                   heading="Add a building")
        auth.audit("building-created", request.form.get("name", ""))
        flash("Building added.", "ok")
        return redirect(url_for("buildings.profile", building_id=building_id))
    return render_template("pages/building_form.html",
                           building={"active": 1}, heading="Add a building")


@bp.route("/<int:building_id>")
@auth.staff_only
def profile(building_id: int):
    building = building_service.get(building_id)
    if building is None:
        flash("That building does not exist.", "error")
        return redirect(url_for("buildings.index"))
    start, end = _span()
    return render_template(
        "pages/building_profile.html",
        building=building,
        agents=building_service.agents_at(building_id),
        stock=building_service.stock_at(building_id),
        history=building_service.history(building_id, 30),
        start=start, end=end,
        figures=next((b for b in building_service.overview(start, end)
                      if b["id"] == building_id), None))


@bp.route("/<int:building_id>/edit", methods=["GET", "POST"])
@auth.staff_only
def edit(building_id: int):
    building = building_service.get(building_id)
    if building is None:
        flash("That building does not exist.", "error")
        return redirect(url_for("buildings.index"))
    if request.method == "POST":
        try:
            building_service.update(
                building_id,
                name=request.form.get("name", ""),
                area=request.form.get("area", ""),
                address=request.form.get("address", ""),
                contact_name=request.form.get("contact_name", ""),
                contact_phone=request.form.get("contact_phone", ""),
                units=parse_int(request.form.get("units")),
                router=request.form.get("router", ""),
                notes=request.form.get("notes", ""),
                active=1 if request.form.get("active") else 0)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("pages/building_form.html",
                                   building=request.form,
                                   heading=f"Edit {building['name']}")
        auth.audit("building-updated", building["name"])
        flash("Building updated.", "ok")
        return redirect(url_for("buildings.profile", building_id=building_id))
    return render_template("pages/building_form.html", building=building,
                           heading=f"Edit {building['name']}")


@bp.route("/<int:building_id>/delete", methods=["POST"])
@auth.staff_only
def delete(building_id: int):
    force = bool(request.form.get("force"))
    try:
        name = building_service.delete(building_id, force=force)
    except ValueError as exc:
        flash(str(exc), "warn")
        return redirect(url_for("buildings.profile", building_id=building_id))
    auth.audit("building-deleted", name)
    flash(f"{name} was removed. Any agents posted there were unposted, not "
          f"deleted.", "ok")
    return redirect(url_for("buildings.index"))