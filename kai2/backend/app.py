"""Application factory.

The back end is this package. The front end is the frontend/ folder beside it
- templates and static files, no Python. Flask is pointed at those folders
explicitly so the split is real rather than a naming convention.
"""

from __future__ import annotations

from datetime import timedelta

from flask import Flask, redirect, render_template, request, url_for

from backend import auth, config, db
from backend.money import (money, pct, pretty_date, qty, signed_money)


def create_app() -> Flask:
    app = Flask(__name__,
                template_folder=str(config.TEMPLATE_DIR),
                static_folder=str(config.STATIC_DIR),
                static_url_path="/static")
    app.config.update(
        SECRET_KEY=config.secret_key(),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_MB * 1024 * 1024,
        TEMPLATES_AUTO_RELOAD=False,
    )

    db.init_db()
    app.teardown_appcontext(db.close_db)

    app.jinja_env.filters.update(money=money, signed_money=signed_money,
                                 qty=qty, day=pretty_date, pct=pct)
    app.jinja_env.globals.update(app_name=config.APP_NAME,
                                 app_version=config.APP_VERSION,
                                 currency=config.CURRENCY,
                                 role_labels=auth.ROLE_LABELS)

    from backend.routes import (agents, daily, dashboard, packages, pos,
                                reports, settings, sign_in, stock)
    for module in (sign_in, dashboard, daily, agents, packages, stock, pos,
                   reports, settings):
        app.register_blueprint(module.bp)

    @app.context_processor
    def inject():
        return {"user": auth.current_user(), "path": request.path}

    @app.errorhandler(404)
    def not_found(_exc):
        return render_template("pages/error.html",
                               heading="That page does not exist",
                               detail="Check the address, or go back."), 404

    @app.errorhandler(413)
    def too_big(_exc):
        return render_template(
            "pages/error.html", heading="That file is too large",
            detail=f"Uploads are limited to {config.MAX_UPLOAD_MB} MB."), 413

    @app.errorhandler(500)
    def failed(_exc):
        return render_template(
            "pages/error.html", heading="Something went wrong",
            detail="Nothing was saved. Try again, and tell your administrator "
                   "what you were doing if it keeps happening."), 500

    @app.route("/")
    def index():
        user = auth.current_user()
        if user is None:
            return redirect(url_for("auth.sign_in"))
        return redirect(auth.home_for(user))

    return app
