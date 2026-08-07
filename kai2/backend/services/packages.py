"""Voucher packages - the denominations on sale.

Packages can be added and edited at any time. The price is copied onto each
voucher when it is taken into stock, so editing a package tomorrow never
changes what yesterday's stock was worth or what an agent already owes on it.
"""

from __future__ import annotations

from backend.db import execute, one, one_dict, rows, scalar

PALETTE = ["#0b6e4f", "#1d4ed8", "#b26b00", "#7c3aed", "#a32015", "#0f766e"]


def all_packages(include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM packages"
    if not include_inactive:
        sql += " WHERE active = 1"
    return rows(sql + " ORDER BY sort_order, price")


def get(package_id: int) -> dict | None:
    return one_dict("SELECT * FROM packages WHERE id = ?", (package_id,))


def by_name(name: str) -> dict | None:
    return one_dict("SELECT * FROM packages WHERE name = ?", (name.strip(),))


def create(name: str, price: int, validity: str = "", prefix: str = "",
           colour: str = "", sort_order: int = 100) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Give the package a name.")
    if price <= 0:
        raise ValueError("The price must be more than zero.")
    if by_name(name):
        raise ValueError(f"There is already a package called {name}.")
    used = scalar("SELECT COUNT(*) FROM packages")
    return execute(
        "INSERT INTO packages (name, price, validity, prefix, colour, sort_order) "
        "VALUES (?,?,?,?,?,?)",
        (name, price, validity.strip(), prefix.strip().upper(),
         colour or PALETTE[used % len(PALETTE)], sort_order),
    )


def update(package_id: int, name: str, price: int, validity: str, prefix: str,
           colour: str, sort_order: int, active: int) -> None:
    if not name.strip():
        raise ValueError("Give the package a name.")
    if price <= 0:
        raise ValueError("The price must be more than zero.")
    clash = one("SELECT id FROM packages WHERE name = ? AND id <> ?",
                (name.strip(), package_id))
    if clash:
        raise ValueError(f"There is already a package called {name.strip()}.")
    execute(
        "UPDATE packages SET name = ?, price = ?, validity = ?, prefix = ?, "
        "colour = ?, sort_order = ?, active = ? WHERE id = ?",
        (name.strip(), price, validity.strip(), prefix.strip().upper(), colour,
         sort_order, 1 if active else 0, package_id),
    )


def delete(package_id: int) -> None:
    """Only if nothing was ever taken in against it; otherwise switch it off,
    so historic stock keeps its meaning."""
    held = scalar("SELECT COUNT(*) FROM vouchers WHERE package_id = ?",
                  (package_id,))
    if held:
        raise ValueError(
            f"That package has {held:,} vouchers on record, so it cannot be "
            f"deleted. Switch it off instead and it will stop being offered.")
    execute("DELETE FROM packages WHERE id = ?", (package_id,))


def with_stock() -> list[dict]:
    """Every package with where its vouchers currently are."""
    return rows(
        "SELECT p.*, "
        " COALESCE(SUM(v.status = 'office'),0) AS in_office, "
        " COALESCE(SUM(v.status = 'agent'),0)  AS with_agents, "
        " COALESCE(SUM(v.status = 'sold'),0)   AS sold, "
        " COALESCE(SUM(v.status = 'dead'),0)   AS dead, "
        " COUNT(v.id) AS total "
        "FROM packages p LEFT JOIN vouchers v ON v.package_id = p.id "
        "GROUP BY p.id ORDER BY p.sort_order, p.price"
    )
