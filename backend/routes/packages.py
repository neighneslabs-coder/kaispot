"""Voucher packages: add, edit, reorder, retire."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from backend import auth
from backend.money import parse_int
from backend.services import packages as package_service

bp = Blueprint("packages", __name__, url_prefix="/packages")


@bp.route("/", methods=["GET", "POST"])
@auth.staff_only
def index():
    if request.method == "POST":
        try:
            package_service.create(
                name=request.form.get("name", ""),
                price=parse_int(request.form.get("price")),
                validity=request.form.get("validity", ""),
                prefix=request.form.get("prefix", ""),
                sort_order=parse_int(request.form.get("sort_order"), 100))
        except ValueError as exc:
            flash(str(exc), "error")
        else:
            auth.audit("package-created", request.form.get("name", ""))
            flash("Package added.", "ok")
        return redirect(url_for("packages.index"))
    return render_template("pages/packages.html",
                           packages=package_service.with_stock(),
                           palette=package_service.PALETTE)


@bp.route("/<int:package_id>", methods=["POST"])
@auth.staff_only
def edit(package_id: int):
    try:
        package_service.update(
            package_id,
            name=request.form.get("name", ""),
            price=parse_int(request.form.get("price")),
            validity=request.form.get("validity", ""),
            prefix=request.form.get("prefix", ""),
            colour=request.form.get("colour", "#0b6e4f"),
            sort_order=parse_int(request.form.get("sort_order"), 100),
            active=1 if request.form.get("active") else 0)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        auth.audit("package-updated", str(package_id))
        flash("Package updated. Vouchers already in stock keep the price they "
              "were taken in at.", "ok")
    return redirect(url_for("packages.index"))


@bp.route("/<int:package_id>/delete", methods=["POST"])
@auth.staff_only
def delete(package_id: int):
    try:
        package_service.delete(package_id)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        auth.audit("package-deleted", str(package_id))
        flash("Package removed.", "ok")
    return redirect(url_for("packages.index"))
