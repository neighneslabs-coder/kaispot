"""The dashboard: how the business and every agent are doing."""

from __future__ import annotations

from flask import Blueprint, render_template, request

from backend import auth
from backend.money import parse_date, today
from backend.services import daily as daily_service
from backend.services import ledger, progress, stock

bp = Blueprint("dashboard", __name__)


@bp.route("/dashboard")
@auth.staff_only
def home():
    day = parse_date(request.args.get("day"), today())
    period = request.args.get("period", "month")
    if period not in ("day", "week", "month", "year"):
        period = "month"

    return render_template(
        "pages/dashboard.html",
        day=day,
        period=period,
        summary=progress.summary(day),
        leaderboard=progress.leaderboard(day, period),
        trend=progress.trend(14),
        months=progress.monthly_series(int(day[:4])),
        board=daily_service.board(day),
        totals=daily_service.day_totals(day),
        outstanding=daily_service.open_days_before(day),
        office=stock.office_stock(),
        stock_totals=stock.totals(),
        findings=progress.for_office(8),
        forecast=progress.forecast(7),
        company=ledger.company_totals(),
        debts=[p for p in ledger.all_positions() if p["debt"] > 0],
    )