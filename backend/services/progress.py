"""Progress and readings: how every agent is doing, and what stands out.

The second half of this file is the model that reads the figures and says
what it sees. It is not a chatbot: that office machine has no route to the
internet, so nothing here can call out to a language model and anything
claiming to would simply fail on the day.

What it is instead is a small statistical model over the figures the app
already keeps. It learns each agent's own normal from their history and
reports where today departs from it - the part of the work a person is bad
at, noticing every day across every agent that a number has drifted.

Every finding carries the numbers it was drawn from, so nothing has to be
taken on trust. A model that says "unusual" without saying "against an
average of 15,889 over 18 days" is asking to be believed rather than
checked.
"""

from __future__ import annotations

from datetime import date, timedelta

from backend.db import one_dict, rows, scalar
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


# =========================================================================
# The readings
# =========================================================================

# How many days of history to learn an agent's normal from.
WINDOW = 21
# How far from their own average counts as worth mentioning.
UNUSUAL = 0.40
# Days of stock left before restocking becomes urgent.
LOW_STOCK_DAYS = 2.0

LEVELS = {"urgent": 3, "warn": 2, "note": 1, "good": 0}


def _finding(level: str, kind: str, headline: str, detail: str,
             agent_id: int | None = None, value: int = 0) -> dict:
    return {"level": level, "kind": kind, "headline": headline,
            "detail": detail, "agent_id": agent_id, "value": value,
            "rank": LEVELS.get(level, 0)}


def _history(agent_id: int, days: int = WINDOW) -> list[dict]:
    start = (date.today() - timedelta(days=days)).isoformat()
    return rows(
        "SELECT day, collection, sold_qty, variance FROM daily_rows "
        "WHERE agent_id = ? AND day >= ? AND collection > 0 ORDER BY day",
        (agent_id, start))


def _mean(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


# --------------------------------------------------------------------------
# The individual readings
# --------------------------------------------------------------------------

def stock_runway(agent_id: int, name: str) -> dict | None:
    """How many days of stock is held, at that agent's own selling rate."""
    held = scalar("SELECT COUNT(*) FROM vouchers WHERE agent_id = ? "
                  "AND status = 'agent'", (agent_id,))
    past = _history(agent_id)
    rate = _mean([h["sold_qty"] for h in past])
    if rate <= 0:
        if held == 0:
            return _finding("warn", "stock", f"{name} has no vouchers",
                            "Nothing to sell and no recent selling rate to "
                            "judge by. Issue a sheet.", agent_id)
        return None
    days = held / rate
    if days <= LOW_STOCK_DAYS:
        return _finding(
            "urgent" if days < 1 else "warn", "stock",
            f"{name} runs out in about {days:.1f} day(s)",
            f"Holding {held:,} card(s) and selling {rate:.1f} a day on "
            f"average over the last {len(past)} trading day(s).",
            agent_id, held)
    if days > 30 and held > 40:
        return _finding(
            "note", "stock", f"{name} is carrying a lot of stock",
            f"{held:,} card(s) at {rate:.1f} a day is about {days:.0f} days' "
            f"worth. Consider moving some to a busier site.", agent_id, held)
    return None


def pace(agent_id: int, name: str) -> dict | None:
    """Whether this month will land on target, at the rate so far.

    Wording stays neutral throughout: agents are not all women, and a report
    that guesses wrong about somebody reads as carelessness.
    """
    agent = one_dict("SELECT monthly_target FROM agents WHERE id = ?",
                     (agent_id,))
    target = (agent or {}).get("monthly_target") or 0
    if not target:
        return None
    start, end = month_bounds(today())
    got = scalar("SELECT COALESCE(SUM(collection),0) FROM daily_rows "
                 "WHERE agent_id = ? AND day BETWEEN ? AND ?",
                 (agent_id, start, today()))
    day_no = date.fromisoformat(today()).day
    days_in = date.fromisoformat(end).day
    if day_no < 3:
        return None
    projected = int(got / day_no * days_in)
    share = projected / target
    if share < 0.75:
        return _finding(
            "warn", "target", f"{name} is heading for {share:.0%} of target",
            f"{got:,} in {day_no} day(s) projects to {projected:,} against a "
            f"target of {target:,}.", agent_id, projected)
    if share >= 1.1:
        return _finding(
            "good", "target", f"{name} is ahead of target",
            f"{got:,} so far projects to {projected:,}, which is "
            f"{share - 1:.0%} above {target:,}.", agent_id, projected)
    return None


def unusual_day(agent_id: int, name: str) -> dict | None:
    """Today measured against that agent's own normal, nobody else's."""
    past = [h for h in _history(agent_id) if h["day"] != today()]
    if len(past) < 5:
        return None
    average = _mean([h["collection"] for h in past])
    if average <= 0:
        return None
    row = one_dict("SELECT collection FROM daily_rows WHERE agent_id = ? "
                   "AND day = ?", (agent_id, today()))
    if row is None or not row["collection"]:
        return None
    swing = (row["collection"] - average) / average
    if swing <= -UNUSUAL:
        return _finding(
            "warn", "unusual", f"{name} is well below the usual day",
            f"{row['collection']:,} today against an average of "
            f"{average:,.0f} over {len(past)} day(s).", agent_id,
            row["collection"])
    if swing >= UNUSUAL:
        return _finding(
            "good", "unusual", f"{name} is well above the usual day",
            f"{row['collection']:,} today against an average of "
            f"{average:,.0f}.", agent_id, row["collection"])
    return None


def shortage_trend(agent_id: int, name: str) -> dict | None:
    """Whether short days are becoming a habit rather than an accident."""
    past = _history(agent_id)
    if len(past) < 4:
        return None
    short = [h for h in past if h["variance"] < 0]
    if len(short) < 2:
        return None
    share = len(short) / len(past)
    total = sum(-h["variance"] for h in short)
    if share >= 0.5:
        return _finding(
            "urgent", "shortage", f"{name} is short on most days",
            f"{len(short)} of the last {len(past)} day(s), {total:,} in total. "
            f"That is a pattern, not an accident.", agent_id, total)
    if share >= 0.25:
        return _finding(
            "warn", "shortage", f"{name} has been short repeatedly",
            f"{len(short)} of the last {len(past)} day(s), {total:,} in total.",
            agent_id, total)
    return None


def idle(agent_id: int, name: str) -> dict | None:
    """An agent who has stopped trading while still holding stock."""
    last = one_dict("SELECT MAX(day) AS day FROM daily_rows WHERE agent_id = ? "
                    "AND collection > 0", (agent_id,))
    held = scalar("SELECT COUNT(*) FROM vouchers WHERE agent_id = ? "
                  "AND status = 'agent'", (agent_id,))
    if not last or not last["day"]:
        return None
    gap = (date.fromisoformat(today()) - date.fromisoformat(last["day"])).days
    if gap >= 3 and held:
        return _finding(
            "warn" if gap < 7 else "urgent", "idle",
            f"{name} has not traded for {gap} day(s)",
            f"Still holding {held:,} card(s). Last takings were on "
            f"{last['day']}.", agent_id, held)
    return None


def debt_watch(agent_id: int, name: str) -> dict | None:
    from backend.services import ledger
    position = ledger.position(agent_id)
    if position["debt"] <= 0:
        return None
    agent = one_dict("SELECT credit_limit FROM agents WHERE id = ?", (agent_id,))
    limit = (agent or {}).get("credit_limit") or 0
    oldest = one_dict("SELECT MIN(entry_date) AS day FROM ledger "
                      "WHERE agent_id = ? AND kind = 'shortage'", (agent_id,))
    age = ""
    if oldest and oldest["day"]:
        days = (date.fromisoformat(today())
                - date.fromisoformat(oldest["day"])).days
        if days > 0:
            age = f" The oldest goes back {days} day(s)."
    if limit and position["debt"] > limit:
        return _finding(
            "urgent", "debt", f"{name} is over the credit limit",
            f"Owing {position['debt']:,} against a limit of {limit:,}."
            f"{age} Clear it before issuing more stock.", agent_id,
            position["debt"])
    return _finding("note", "debt", f"{name} owes {position['debt']:,}",
                    f"Commission of {position['commission_due']:,} is owed "
                    f"back, which could settle part of it.{age}",
                    agent_id, position["debt"])


# --------------------------------------------------------------------------
# Putting it together
# --------------------------------------------------------------------------

def for_agent(agent_id: int, name: str = "") -> list[dict]:
    """Everything worth saying about one agent, most serious first."""
    if not name:
        row = one_dict("SELECT name FROM agents WHERE id = ?", (agent_id,))
        name = (row or {}).get("name", "This agent")
    first = name.split()[0] if name else "She"
    out = []
    for check in (stock_runway, pace, unusual_day, shortage_trend, idle,
                  debt_watch):
        try:
            found = check(agent_id, first)
        except Exception:
            found = None          # one bad reading must not blank the page
        if found:
            out.append(found)
    out.sort(key=lambda f: -f["rank"])
    return out


def for_office(limit: int = 8) -> list[dict]:
    """The whole business, worst first."""
    out: list[dict] = []
    for agent in rows("SELECT id, name FROM agents WHERE active = 1"):
        out.extend(for_agent(agent["id"], agent["name"]))
    out.extend(business_wide())
    out.sort(key=lambda f: (-f["rank"], -f["value"]))
    return out[:limit]


def business_wide() -> list[dict]:
    """Findings about the company rather than any one person."""
    out = []
    start, end = week_bounds(today())
    this_week = scalar("SELECT COALESCE(SUM(collection),0) FROM daily_rows "
                       "WHERE day BETWEEN ? AND ?", (start, end))
    previous = scalar(
        "SELECT COALESCE(SUM(collection),0) FROM daily_rows "
        "WHERE day BETWEEN date(?, '-7 day') AND date(?, '-7 day')",
        (start, end))
    if previous and this_week:
        swing = (this_week - previous) / previous
        if swing <= -0.25:
            out.append(_finding(
                "warn", "trend", "Takings are down on last week",
                f"{this_week:,} so far against {previous:,} in the same days "
                f"last week, {abs(swing):.0%} lower.", None, previous - this_week))
        elif swing >= 0.25:
            out.append(_finding(
                "good", "trend", "Takings are up on last week",
                f"{this_week:,} against {previous:,} last week.", None,
                this_week - previous))

    unclosed = scalar("SELECT COUNT(*) FROM daily_rows WHERE status = 'open' "
                      "AND day < ?", (today(),))
    if unclosed:
        out.append(_finding(
            "warn", "housekeeping", f"{unclosed} earlier day(s) are still open",
            "A day left open never turns its shortage into a debt, so the "
            "figures drift quietly out of step.", None, unclosed))

    shelf = scalar("SELECT COUNT(*) FROM vouchers WHERE status = 'office'")
    if shelf == 0:
        with_agents = scalar("SELECT COUNT(*) FROM vouchers "
                             "WHERE status = 'agent'")
        if with_agents < 50:
            out.append(_finding(
                "warn", "stock", "The office shelf is empty",
                f"Only {with_agents:,} card(s) are out with agents and there "
                f"is nothing left to issue. Register the next print run.",
                None, with_agents))
    return out


def forecast(days: int = 7) -> dict:
    """What the coming week looks like, from the last four weeks.

    Each weekday is projected from the same weekday before it, because trade
    is not flat across a week - a Sunday and a Friday are different animals
    and averaging them together hides both.
    """
    history = rows(
        "SELECT day, COALESCE(SUM(collection),0) AS collection FROM daily_rows "
        "WHERE day >= date(?, '-28 day') GROUP BY day", (today(),))
    by_weekday: dict[int, list[int]] = {}
    for row in history:
        weekday = date.fromisoformat(row["day"]).weekday()
        by_weekday.setdefault(weekday, []).append(row["collection"])
    overall = _mean([r["collection"] for r in history]) if history else 0

    out = []
    total = 0
    for step in range(1, days + 1):
        when = date.fromisoformat(today()) + timedelta(days=step)
        seen = by_weekday.get(when.weekday(), [])
        expected = int(_mean(seen) if seen else overall)
        total += expected
        out.append({"day": when.isoformat(), "label": when.strftime("%a"),
                    "expected": expected, "from_days": len(seen)})
    return {"days": out, "total": total,
            "confident": bool(history) and len(history) >= 7}