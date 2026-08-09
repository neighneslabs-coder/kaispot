"""Reports and CSV export."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from flask import Blueprint, Response, render_template, request

from backend import auth
from backend.money import parse_date, today
from backend.services import buildings as building_service
from backend.services import ledger, progress, sales

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _range() -> tuple[str, str]:
    end = parse_date(request.args.get("end"), today())
    start = parse_date(request.args.get("start"),
                       (date.today() - timedelta(days=29)).isoformat())
    return (end, start) if start > end else (start, end)


@bp.route("/")
@auth.staff_only
def index():
    start, end = _range()
    return render_template(
        "pages/reports.html", start=start, end=end,
        period=request.args.get("period", "month"),
        leaderboard=progress.leaderboard(end, request.args.get("period", "month")),
        packages=progress.package_sales(start, end),
        channels=sales.by_channel(start, end),
        months=progress.monthly_series(int(end[:4])),
        positions=ledger.all_positions(),
        company=ledger.company_totals())


@bp.route("/export/<what>.csv")
@auth.staff_only
def export(what: str):
    start, end = _range()
    if what == "agents":
        board = progress.leaderboard(end, request.args.get("period", "month"))
        data = board["rows"]
        columns = [("code", "Code"), ("name", "Agent"), ("station", "Station"),
                   ("days", "Days"), ("sold", "Sold"),
                   ("collection", "Collected"), ("shortage", "Shortage"),
                   ("commission", "Commission"), ("target", "Target")]
    elif what == "packages":
        data = progress.package_sales(start, end)
        columns = [("name", "Package"), ("price", "Price"), ("qty", "Sold"),
                   ("value", "Value")]
    elif what == "buildings":
        data = building_service.overview(start, end)
        columns = [("name", "Building"), ("area", "Area"), ("units", "Units"),
                   ("agents", "Agents"), ("sold_qty", "Vouchers sold"),
                   ("collection", "Collected"), ("shortage", "Shortage"),
                   ("commission", "Commission")]
    elif what == "debts":
        data = ledger.all_positions()
        columns = [("code", "Code"), ("name", "Agent"), ("debt", "Owes"),
                   ("commission_due", "Commission due"), ("net", "Net")]
    else:
        data, columns = [], [("", "")]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([heading for _, heading in columns])
    for row in data:
        writer.writerow([row.get(key, "") for key, _ in columns])

    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="kaispot-{what}-{start}-'
                             f'to-{end}.csv"'})