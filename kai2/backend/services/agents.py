"""Sales agents: their record, their targets, and their standing."""

from __future__ import annotations

from backend.db import execute, one, one_dict, rows, scalar
from backend.money import month_bounds, today, year_bounds


def all_agents(include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM agents"
    if not include_inactive:
        sql += " WHERE active = 1"
    return rows(sql + " ORDER BY name")


def get(agent_id: int) -> dict | None:
    return one_dict("SELECT * FROM agents WHERE id = ?", (agent_id,))


def create(code: str, name: str, phone: str = "", station: str = "",
           commission_rate: int = 1000, monthly_target: int = 0,
           daily_target: int = 0, credit_limit: int = 0, nin: str = "",
           next_of_kin: str = "", notes: str = "") -> int:
    code = code.strip().upper()
    name = name.strip()
    if not code:
        raise ValueError("Give the agent a short code, such as A-01.")
    if not name:
        raise ValueError("Give the agent's full name.")
    if one("SELECT id FROM agents WHERE code = ?", (code,)):
        raise ValueError(f"The code {code} is already taken.")
    if not 0 <= commission_rate <= 10_000:
        raise ValueError("Commission must be between 0% and 100%.")
    return execute(
        "INSERT INTO agents (code, name, phone, station, commission_rate, "
        "monthly_target, daily_target, credit_limit, nin, next_of_kin, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (code, name, phone.strip(), station.strip(), commission_rate,
         max(0, monthly_target), max(0, daily_target), max(0, credit_limit),
         nin.strip(), next_of_kin.strip(), notes.strip()),
    )


def update(agent_id: int, **fields) -> None:
    agent = get(agent_id)
    if agent is None:
        raise ValueError("That agent does not exist.")
    if not str(fields.get("name", "")).strip():
        raise ValueError("Give the agent's full name.")
    rate = int(fields.get("commission_rate", agent["commission_rate"]))
    if not 0 <= rate <= 10_000:
        raise ValueError("Commission must be between 0% and 100%.")
    execute(
        "UPDATE agents SET name = ?, phone = ?, station = ?, commission_rate = ?, "
        "monthly_target = ?, daily_target = ?, credit_limit = ?, nin = ?, "
        "next_of_kin = ?, notes = ?, active = ? WHERE id = ?",
        (str(fields["name"]).strip(), str(fields.get("phone", "")).strip(),
         str(fields.get("station", "")).strip(), rate,
         max(0, int(fields.get("monthly_target", 0))),
         max(0, int(fields.get("daily_target", 0))),
         max(0, int(fields.get("credit_limit", 0))),
         str(fields.get("nin", "")).strip(),
         str(fields.get("next_of_kin", "")).strip(),
         str(fields.get("notes", "")).strip(),
         1 if fields.get("active") else 0, agent_id),
    )


def delete(agent_id: int) -> str:
    """Remove an agent outright if they never traded; otherwise deactivate.

    An agent who has held stock or owed money cannot simply vanish - the
    figures behind them have to keep meaning something.
    """
    agent = get(agent_id)
    if agent is None:
        raise ValueError("That agent does not exist.")
    traded = (scalar("SELECT COUNT(*) FROM daily_rows WHERE agent_id = ?", (agent_id,))
              + scalar("SELECT COUNT(*) FROM ledger WHERE agent_id = ?", (agent_id,))
              + scalar("SELECT COUNT(*) FROM vouchers WHERE agent_id = ?", (agent_id,)))
    if traded:
        execute("UPDATE agents SET active = 0 WHERE id = ?", (agent_id,))
        execute("UPDATE users SET active = 0 WHERE agent_id = ?", (agent_id,))
        return "deactivated"
    execute("DELETE FROM users WHERE agent_id = ?", (agent_id,))
    execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    return "deleted"


def stock_value(agent_id: int) -> int:
    return scalar("SELECT COALESCE(SUM(price),0) FROM vouchers "
                  "WHERE agent_id = ? AND status = 'agent'", (agent_id,))


def stock_by_package(agent_id: int) -> list[dict]:
    return rows(
        "SELECT p.id, p.name, p.price, p.colour, p.validity, COUNT(v.id) AS qty, "
        "COALESCE(SUM(v.price),0) AS value FROM vouchers v "
        "JOIN packages p ON p.id = v.package_id "
        "WHERE v.agent_id = ? AND v.status = 'agent' "
        "GROUP BY p.id ORDER BY p.sort_order, p.price",
        (agent_id,),
    )


def sold_between(agent_id: int, start: str, end: str) -> dict:
    row = one(
        "SELECT COUNT(*) AS qty, COALESCE(SUM(price),0) AS value FROM vouchers "
        "WHERE agent_id = ? AND status = 'sold' AND sold_on BETWEEN ? AND ?",
        (agent_id, start, end),
    )
    return dict(row) if row else {"qty": 0, "value": 0}


def performance(agent_id: int, day: str | None = None) -> dict:
    """Collections and shortages over the usual periods."""
    day = day or today()
    from backend.money import week_bounds
    spans = {
        "day": (day, day),
        "week": week_bounds(day),
        "month": month_bounds(day),
        "year": year_bounds(day),
    }
    out: dict[str, dict] = {}
    for label, (start, end) in spans.items():
        row = one(
            "SELECT COALESCE(SUM(collection),0) AS collection, "
            "COALESCE(SUM(sold),0) AS sold, "
            "COALESCE(SUM(shortage),0) AS shortage, "
            "COALESCE(SUM(commission),0) AS commission, "
            "COUNT(*) AS days FROM daily_rows "
            "WHERE agent_id = ? AND day BETWEEN ? AND ?",
            (agent_id, start, end),
        )
        out[label] = dict(row) if row else {}
        out[label]["start"], out[label]["end"] = start, end
    agent = get(agent_id) or {}
    target = agent.get("monthly_target") or 0
    collected = out["month"]["collection"]
    out["month"]["target"] = target
    out["month"]["target_pct"] = round(100 * collected / target) if target else None
    daily_target = agent.get("daily_target") or 0
    out["day"]["target"] = daily_target
    out["day"]["target_pct"] = (round(100 * out["day"]["collection"] / daily_target)
                                if daily_target else None)
    return out
