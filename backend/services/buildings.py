"""Buildings: the sites the company sells in.

An agent is posted to a building, so takings, shortages and stock can be read
per site as well as per person. That distinction matters: a weak month at one
address is a different problem from a weak month for one person, and only
seeing both apart tells you which you have.

A building outlives the agent covering it, so deleting an agent never deletes
the site's history.
"""

from __future__ import annotations

from backend.db import execute, one, one_dict, rows, scalar
from backend.money import month_bounds, today


def all_buildings(include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM buildings"
    if not include_inactive:
        sql += " WHERE active = 1"
    return rows(sql + " ORDER BY name COLLATE NOCASE")


def get(building_id: int) -> dict | None:
    return one_dict("SELECT * FROM buildings WHERE id = ?", (building_id,))


def by_name(name: str) -> dict | None:
    return one_dict("SELECT * FROM buildings WHERE name = ?", (name.strip(),))


def create(name: str, area: str = "", address: str = "", contact_name: str = "",
           contact_phone: str = "", units: int = 0, router: str = "",
           notes: str = "") -> int:
    name = name.strip()
    if not name:
        raise ValueError("Give the building a name.")
    if by_name(name):
        raise ValueError(f"There is already a building called {name}.")
    return execute(
        "INSERT INTO buildings (name, area, address, contact_name, "
        "contact_phone, units, router, notes) VALUES (?,?,?,?,?,?,?,?)",
        (name, area.strip(), address.strip(), contact_name.strip(),
         contact_phone.strip(), max(0, int(units)), router.strip(),
         notes.strip()))


def update(building_id: int, name: str, area: str = "", address: str = "",
           contact_name: str = "", contact_phone: str = "", units: int = 0,
           router: str = "", notes: str = "", active: int = 1) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Give the building a name.")
    clash = one("SELECT id FROM buildings WHERE name = ? AND id <> ?",
                (name, building_id))
    if clash:
        raise ValueError(f"There is already a building called {name}.")
    execute(
        "UPDATE buildings SET name = ?, area = ?, address = ?, "
        "contact_name = ?, contact_phone = ?, units = ?, router = ?, "
        "notes = ?, active = ? WHERE id = ?",
        (name, area.strip(), address.strip(), contact_name.strip(),
         contact_phone.strip(), max(0, int(units)), router.strip(),
         notes.strip(), 1 if active else 0, building_id))


def agents_at(building_id: int) -> list[dict]:
    return rows("SELECT * FROM agents WHERE building_id = ? ORDER BY name",
                (building_id,))


def delete(building_id: int, force: bool = False) -> str:
    """Remove a building.

    Agents posted there are unposted, never deleted - a person is not a
    property. With agents still attached, the caller has to say so
    explicitly, so nobody empties a site by accident.
    """
    building = get(building_id)
    if building is None:
        raise ValueError("That building does not exist.")
    posted = scalar("SELECT COUNT(*) FROM agents WHERE building_id = ?",
                    (building_id,))
    if posted and not force:
        raise ValueError(
            f"{building['name']} still has {posted} agent(s) posted to it. "
            f"Move them first, or confirm to unpost them and delete it.")
    execute("UPDATE agents SET building_id = NULL WHERE building_id = ?",
            (building_id,))
    execute("DELETE FROM buildings WHERE id = ?", (building_id,))
    return building["name"]


def overview(start: str | None = None, end: str | None = None) -> list[dict]:
    """Every building with how it is trading.

    Agents with no building are gathered into one unnamed line, so the totals
    across the page still add up to the whole business.
    """
    if start is None or end is None:
        start, end = month_bounds(today())
    return rows(
        "SELECT b.id, b.name, b.area, b.units, b.active, "
        " COUNT(DISTINCT a.id) AS agents, "
        " COALESCE(SUM(d.collection),0) AS collection, "
        " COALESCE(SUM(d.sold_qty),0) AS sold_qty, "
        " COALESCE(SUM(CASE WHEN d.variance < 0 THEN -d.variance ELSE 0 END),0) "
        "   AS shortage, "
        " COALESCE(SUM(d.commission),0) AS commission "
        "FROM buildings b "
        "LEFT JOIN agents a ON a.building_id = b.id AND a.active = 1 "
        "LEFT JOIN daily_rows d ON d.agent_id = a.id AND d.day BETWEEN ? AND ? "
        "GROUP BY b.id ORDER BY collection DESC, b.name",
        (start, end))


def unposted(start: str | None = None, end: str | None = None) -> dict:
    """The agents belonging to no building, as one line."""
    if start is None or end is None:
        start, end = month_bounds(today())
    row = one_dict(
        "SELECT COUNT(DISTINCT a.id) AS agents, "
        " COALESCE(SUM(d.collection),0) AS collection, "
        " COALESCE(SUM(d.sold_qty),0) AS sold_qty, "
        " COALESCE(SUM(CASE WHEN d.variance < 0 THEN -d.variance ELSE 0 END),0) "
        "   AS shortage "
        "FROM agents a LEFT JOIN daily_rows d "
        "  ON d.agent_id = a.id AND d.day BETWEEN ? AND ? "
        "WHERE a.building_id IS NULL AND a.active = 1", (start, end))
    return row or {"agents": 0, "collection": 0, "sold_qty": 0, "shortage": 0}


def stock_at(building_id: int) -> dict:
    """Voucher stock held by the agents posted to this building."""
    row = one_dict(
        "SELECT COUNT(*) AS qty, COALESCE(SUM(v.price),0) AS value "
        "FROM vouchers v JOIN agents a ON a.id = v.agent_id "
        "WHERE a.building_id = ? AND v.status = 'agent'", (building_id,))
    return row or {"qty": 0, "value": 0}


def history(building_id: int, limit: int = 60) -> list[dict]:
    """Day by day for the whole site."""
    return rows(
        "SELECT d.day, COALESCE(SUM(d.collection),0) AS collection, "
        " COALESCE(SUM(d.sold_qty),0) AS sold_qty, "
        " COALESCE(SUM(d.variance),0) AS variance, "
        " COUNT(DISTINCT d.agent_id) AS agents "
        "FROM daily_rows d JOIN agents a ON a.id = d.agent_id "
        "WHERE a.building_id = ? GROUP BY d.day ORDER BY d.day DESC LIMIT ?",
        (building_id, limit))