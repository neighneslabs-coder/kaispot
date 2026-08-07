"""Sign in, sign out, change password."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from backend import auth

bp = Blueprint("auth", __name__)


@bp.route("/sign-in", methods=["GET", "POST"])
def sign_in():
    if auth.current_user() is not None:
        return redirect(auth.home_for(auth.current_user()))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        user = auth.authenticate(username, request.form.get("password", ""))
        if user is None:
            flash("That username and password do not match an account.", "error")
            return render_template("pages/sign_in.html", username=username), 401
        auth.sign_in(user)
        auth.audit("sign-in", username)
        if user["must_change"]:
            flash("Set your own password before going any further.", "warn")
            return redirect(url_for("auth.password"))
        return redirect(auth.home_for(user))

    return render_template("pages/sign_in.html", username="")


@bp.route("/sign-out")
def sign_out():
    auth.sign_out()
    return redirect(url_for("auth.sign_in"))


@bp.route("/password", methods=["GET", "POST"])
@auth.login_required
def password():
    user = auth.current_user()
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        if not user["must_change"] and not auth.verify_password(
                current, user["password_hash"]):
            flash("Your current password is not right.", "error")
        elif len(new) < 6:
            flash("Use at least 6 characters.", "error")
        elif new != request.form.get("confirm", ""):
            flash("The two new passwords are different.", "error")
        else:
            auth.set_password(user["id"], new, must_change=0)
            auth.audit("password-change", user["username"])
            flash("Password changed.", "ok")
            return redirect(auth.home_for(user))
    return render_template("pages/password.html", forced=bool(user["must_change"]))
