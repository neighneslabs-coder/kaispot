"""Progress: how every agent is doing over day, week, month and year."""

from __future__ import annotations

from datetime import date, timedelta

from backend.db import rows, scalar
from backend.money import month_bounds, today, week_bounds, year_bounds
from backend.services import agents as agent_service, ledger


def _span(start: str, end: str) -> list[dict]:
    return rows(
        "SELECT a.id, a.code, a.name, a.station, a.monthly_target, "
        " a.daily_target, a.commission_rate, "
        " COALESCE(SUM(d.collection),0) AS collection, "
        " COALESCE(SUM(d.sold),0) AS sold, "
        " COALESCE(SUM(d.shortage),0) AS shortage, "
        " COALESCE(SUM(d.commission),0) AS commission, "
        " COUNT(d.id) AS days "
        "FROM agents a LEFT JOIN daily_rows d ON d.agent_id = a.id "
        "  AND d.day BETWEEN ? AND ? "
        "WHERE a.active = 1 GROUP BY a.id ORDER BY collection DESC",
        (start, end))


def leaderboard(day: str | None = None, period: str = "month") -> dict:
    day = day or today()
    spans = {"day": (day, day), "week": week_bounds(day),
             "month": month_bounds(day), "year": year_bounds(day)}
    start, end = spans.get(period, spans["month"])
    lines = _span(start, end)
    target_field = "daily_target" if period == "day" else "monthly_target"
    for row in lines:
        target = row[target_field] if period in ("day", "month") else 0
        row["target"] = target
        row["target_pct"] = (round(100 * row["collection"] / target)
                             if target else None)
    return {"period": period, "start": start, "end": end, "rows": lines,
            "collection": sum(r["collection"] for r in lines),
            "shortage": sum(r["shortage"] for r in lines if r["shortage"] > 0),
            "commission": sum(r["commission"] for r in lines)}


def agent_periods(agent_id: int, day: str | None = None) -> dict:
    return agent_service.performance(agent_id, day)


def trend(days: int = 14) -> list[dict]:
    """Daily collection across the whole business, oldest first, gaps filled."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    found = {r["day"]: r["collection"] for r in rows(
        "SELECT day, COALESCE(SUM(collection),0) AS collection FROM daily_rows "
        "WHERE day BETWEEN ? AND ? GROUP BY day",
        (start.isoformat(), end.isoformat()))}
    return [{"day": (start + timedelta(days=i)).isoformat(),
             "collection": found.get((start + timedelta(days=i)).isoformat(), 0)}
            for i in range(days)]


def monthly_series(year: int) -> list[dict]:
    found = {r["ym"]: r for r in rows(
        "SELECT substr(day,1,7) AS ym, COALESCE(SUM(collection),0) AS collection, "
        "COALESCE(SUM(shortage),0) AS shortage FROM daily_rows "
        "WHERE day BETWEEN ? AND ? GROUP BY ym",
        (f"{year}-01-01", f"{year}-12-31"))}
    out = []
    for month in range(1, 13):
        key = f"{year}-{month:02d}"
        row = found.get(key, {})
        out.append({"month": key,
                    "label": date(year, month, 1).strftime("%b"),
                    "collection": row.get("collection", 0),
                    "shortage": row.get("shortage", 0)})
    return out


def package_sales(start: str, end: str) -> list[dict]:
    return rows(
        "SELECT p.name, p.price, p.colour, COUNT(v.id) AS qty, "
        "COALESCE(SUM(v.price),0) AS value FROM vouchers v "
        "JOIN packages p ON p.id = v.package_id "
        "WHERE v.status = 'sold' AND v.sold_on BETWEEN ? AND ? "
        "GROUP BY p.id ORDER BY value DESC", (start, end))


def summary(day: str | None = None) -> dict:
    day = day or today()
    week = week_bounds(day)
    month = month_bounds(day)
    year = year_bounds(day)

    def total(start: str, end: str, field: str = "collection") -> int:
        return scalar(f"SELECT COALESCE(SUM({field}),0) FROM daily_rows "
                      f"WHERE day BETWEEN ? AND ?", (start, end))

    return {
        "day": {"collection": total(day, day),
                "shortage": total(day, day, "shortage")},
        "week": {"collection": total(*week), "shortage": total(*week, "shortage")},
        "month": {"collection": total(*month),
                  "shortage": total(*month, "shortage")},
        "year": {"collection": total(*year), "shortage": total(*year, "shortage")},
        "company": ledger.company_totals(),
    }
