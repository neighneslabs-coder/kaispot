"""Voucher stock: taking it in, and handing it to agents.

The price belongs to the batch, not to the package. A sheet printed at 1,000
is worth 1,000 for as long as those papers are in the field, whatever the
package is repriced to afterwards. So the price is chosen when a batch is
registered and frozen onto every voucher in it. Two runs of the same package
at different prices can be out at once and both stay right, and no past day's
figures are ever rewritten by a price change.

Whichever way a voucher arrives, its code is stored once and the code column
is unique across the whole table. That is what stops the same voucher being
taken in twice.
"""

from __future__ import annotations

import re

from backend.db import execute, one, one_dict, rows, scalar, transaction
from backend.services import packages as package_service

RANGE = re.compile(r"^\s*([A-Za-z-]*)(\d+)\s*(?:-|to|\.\.)\s*([A-Za-z-]*)(\d+)\s*$")
MAX_RANGE = 5000


# --------------------------------------------------------------------------
# Reading typed input
# --------------------------------------------------------------------------

def split_codes(raw: str) -> list[str]:
    """Turn typed input into codes.

    Accepts one per line, several to a line, and ranges such as
    'KS0001-KS0100', which expand with the numbering width preserved.
    """
    found: list[str] = []
    seen: set[str] = set()
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        span = RANGE.match(line)
        if span:
            prefix, low, prefix2, high = span.groups()
            if prefix2 and prefix2 != prefix:
                prefix = prefix2
            width = len(low)
            start, stop = int(low), int(high)
            if stop < start:
                start, stop = stop, start
            if stop - start >= MAX_RANGE:
                raise ValueError(
                    f"That range covers {stop - start + 1:,} vouchers. Split it "
                    f"into smaller runs of at most {MAX_RANGE:,}.")
            for number in range(start, stop + 1):
                code = f"{prefix}{number:0{width}d}"
                if code.lower() not in seen:
                    seen.add(code.lower())
                    found.append(code)
            continue
        for token in re.split(r"[\s,;]+", line):
            token = token.strip()
            if token and token.lower() not in seen:
                seen.add(token.lower())
                found.append(token)
    return found


def as_entries(items: list) -> list[dict]:
    """Accept either plain codes or code/password pairs."""
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append({"code": str(item.get("code", "")).strip(),
                        "secret": str(item.get("secret", "")).strip()})
        else:
            out.append({"code": str(item).strip(), "secret": ""})
    return [e for e in out if e["code"]]


def known_codes(codes: list[str]) -> set[str]:
    """Which of these are already on record, in any state."""
    if not codes:
        return set()
    found: set[str] = set()
    for start in range(0, len(codes), 400):
        chunk = codes[start:start + 400]
        marks = ",".join("?" for _ in chunk)
        for row in rows(f"SELECT code FROM vouchers WHERE code IN ({marks})",
                        tuple(chunk)):
            found.add(row["code"].lower())
    return found


# --------------------------------------------------------------------------
# Taking stock in
# --------------------------------------------------------------------------

def take_in(package_id: int, codes: list, kind: str, received_on: str,
            price: int | None = None, reference: str = "", note: str = "",
            filename: str = "", stored_name: str = "", pages: int = 0,
            agent_id: int | None = None, user_id: int | None = None) -> dict:
    """Register a batch of vouchers.

    `price` is what is printed on these particular papers. Left out, the
    package's current price is used. Duplicates are skipped, never merged.

    Giving an `agent_id` registers the batch and hands it straight to that
    agent in one action, which is what happens when a sheet is uploaded for
    somebody: the sheet is her stock.
    """
    package = package_service.get(package_id)
    if package is None:
        raise ValueError("Choose a package first.")

    unit = package["price"] if price is None else int(price)
    if unit <= 0:
        raise ValueError("The price on this batch must be more than zero.")

    entries = as_entries(codes)
    if not entries:
        raise ValueError("No voucher numbers were found to take in.")

    already = known_codes([e["code"] for e in entries])
    fresh = [e for e in entries if e["code"].lower() not in already]

    agent = None
    if agent_id:
        agent = one_dict("SELECT * FROM agents WHERE id = ? AND active = 1",
                         (agent_id,))
        if agent is None:
            raise ValueError("Choose an active agent to give this batch to.")

    with transaction():
        intake_id = execute(
            "INSERT INTO intakes (kind, package_id, reference, filename, "
            "stored_name, pages, accepted, duplicates, note, received_on, "
            "created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (kind, package_id, reference.strip(), filename, stored_name, pages,
             len(fresh), len(already), note.strip(), received_on, user_id))

        status = "agent" if agent else "office"
        for entry in fresh:
            execute(
                "INSERT INTO vouchers (package_id, intake_id, code, secret, "
                "price, status, agent_id, assigned_on) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (package_id, intake_id, entry["code"], entry["secret"], unit,
                 status, agent_id if agent else None,
                 received_on if agent else None))

        if agent and fresh:
            execute(
                "INSERT INTO assignments (agent_id, package_id, qty, value, "
                "assigned_on, note, created_by) VALUES (?,?,?,?,?,?,?)",
                (agent_id, package_id, len(fresh), len(fresh) * unit,
                 received_on, (note.strip() or f"Sheet {filename}".strip()),
                 user_id))

    return {
        "intake_id": intake_id,
        "accepted": len(fresh),
        "duplicates": len(already),
        "price": unit,
        "value": len(fresh) * unit,
        "package": package,
        "agent": agent,
    }


# --------------------------------------------------------------------------
# Where the stock is
# --------------------------------------------------------------------------

def office_stock() -> list[dict]:
    """What is in the office, by package.

    `value` adds up the price actually frozen on each voucher rather than
    multiplying by the package price, so a package holding two runs at
    different prices still totals correctly. `price_runs` says how many
    distinct prices are sitting in there, which is worth showing.
    """
    return rows(
        "SELECT p.id, p.name, p.price, p.colour, p.validity, "
        " COUNT(v.id) AS qty, "
        " COALESCE(SUM(v.price),0) AS value, "
        " COUNT(DISTINCT v.price) AS price_runs, "
        " MIN(v.price) AS low_price, MAX(v.price) AS high_price "
        "FROM packages p LEFT JOIN vouchers v "
        "  ON v.package_id = p.id AND v.status = 'office' "
        "WHERE p.active = 1 GROUP BY p.id ORDER BY p.sort_order, p.price")


def office_count(package_id: int, price: int | None = None) -> int:
    if price is None:
        return scalar("SELECT COUNT(*) FROM vouchers WHERE package_id = ? "
                      "AND status = 'office'", (package_id,))
    return scalar("SELECT COUNT(*) FROM vouchers WHERE package_id = ? "
                  "AND status = 'office' AND price = ?", (package_id, price))


def held_by(agent_id: int) -> list[dict]:
    """An agent's unsold stock, split by package and by price.

    Split by price because a day is balanced against what a voucher is worth,
    and she may be carrying two runs of the same package at different prices.
    """
    return rows(
        "SELECT v.package_id, p.name, p.colour, p.validity, v.price, "
        " COUNT(*) AS qty, SUM(v.price) AS value "
        "FROM vouchers v JOIN packages p ON p.id = v.package_id "
        "WHERE v.agent_id = ? AND v.status = 'agent' "
        "GROUP BY v.package_id, v.price ORDER BY p.sort_order, v.price",
        (agent_id,))


# --------------------------------------------------------------------------
# Giving it out and taking it back
# --------------------------------------------------------------------------

def assign(agent_id: int, package_id: int, qty: int, assigned_on: str,
           price: int | None = None, note: str = "",
           user_id: int | None = None) -> dict:
    """Hand vouchers to an agent. This is the top-up on the daily table.

    Oldest first, so the papers printed earliest leave the office first.
    """
    if qty <= 0:
        raise ValueError("Enter how many vouchers to give out.")
    agent = one_dict("SELECT * FROM agents WHERE id = ? AND active = 1",
                     (agent_id,))
    if agent is None:
        raise ValueError("Choose an active agent.")
    package = package_service.get(package_id)
    if package is None:
        raise ValueError("Choose a package.")

    available = office_count(package_id, price)
    if qty > available:
        at = f" at {price:,}" if price is not None else ""
        raise ValueError(
            f"Only {available:,} {package['name']} vouchers{at} are in the "
            f"office. Take more in before giving out {qty:,}.")

    with transaction():
        if price is None:
            picked = rows(
                "SELECT id, price FROM vouchers WHERE package_id = ? AND "
                "status = 'office' ORDER BY id LIMIT ?", (package_id, qty))
        else:
            picked = rows(
                "SELECT id, price FROM vouchers WHERE package_id = ? AND "
                "status = 'office' AND price = ? ORDER BY id LIMIT ?",
                (package_id, price, qty))
        value = sum(v["price"] for v in picked)
        marks = ",".join("?" for _ in picked)
        execute(
            f"UPDATE vouchers SET status = 'agent', agent_id = ?, "
            f"assigned_on = ? WHERE id IN ({marks})",
            (agent_id, assigned_on, *[v["id"] for v in picked]))
        execute(
            "INSERT INTO assignments (agent_id, package_id, qty, value, "
            "assigned_on, note, created_by) VALUES (?,?,?,?,?,?,?)",
            (agent_id, package_id, len(picked), value, assigned_on,
             note.strip(), user_id))
    return {"qty": len(picked), "value": value, "package": package,
            "agent": agent}


def recall(agent_id: int, package_id: int, qty: int, assigned_on: str,
           user_id: int | None = None) -> dict:
    """Take unsold vouchers back off an agent, into the office."""
    if qty <= 0:
        raise ValueError("Enter how many vouchers to take back.")
    held = scalar("SELECT COUNT(*) FROM vouchers WHERE agent_id = ? AND "
                  "package_id = ? AND status = 'agent'", (agent_id, package_id))
    if qty > held:
        raise ValueError(f"That agent only holds {held:,} of those.")
    with transaction():
        picked = rows(
            "SELECT id, price FROM vouchers WHERE agent_id = ? AND "
            "package_id = ? AND status = 'agent' ORDER BY id DESC LIMIT ?",
            (agent_id, package_id, qty))
        value = sum(v["price"] for v in picked)
        marks = ",".join("?" for _ in picked)
        execute(f"UPDATE vouchers SET status = 'office', agent_id = NULL, "
                f"assigned_on = NULL WHERE id IN ({marks})",
                tuple(v["id"] for v in picked))
        execute(
            "INSERT INTO assignments (agent_id, package_id, qty, value, "
            "assigned_on, note, created_by) VALUES (?,?,?,?,?,?,?)",
            (agent_id, package_id, -len(picked), -value, assigned_on,
             "Returned to office", user_id))
    return {"qty": len(picked), "value": value}


def topup_value(agent_id: int, day: str) -> int:
    """Value handed to this agent on this day, net of returns."""
    return scalar("SELECT COALESCE(SUM(value),0) FROM assignments "
                  "WHERE agent_id = ? AND assigned_on = ?", (agent_id, day))


def topup_qty(agent_id: int, day: str) -> int:
    """Number of vouchers handed over on this day, net of returns."""
    return scalar("SELECT COALESCE(SUM(qty),0) FROM assignments "
                  "WHERE agent_id = ? AND assigned_on = ?", (agent_id, day))


def topup_lines(agent_id: int, day: str) -> list[dict]:
    """The day's top-up broken down by package and price."""
    return rows(
        "SELECT package_id, price, COUNT(*) AS qty, SUM(price) AS value "
        "FROM vouchers WHERE agent_id = ? AND assigned_on = ? "
        "GROUP BY package_id, price", (agent_id, day))


def mark_dead(voucher_ids: list[int], user_id: int | None = None) -> int:
    """Write off vouchers that cannot be sold - misprinted, torn, expired."""
    if not voucher_ids:
        return 0
    marks = ",".join("?" for _ in voucher_ids)
    execute(f"UPDATE vouchers SET status = 'dead' WHERE id IN ({marks}) "
            f"AND status <> 'sold'", tuple(voucher_ids))
    return len(voucher_ids)


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------

def intakes(limit: int = 50) -> list[dict]:
    """Batches registered, with the price each was registered at.

    The price is read back off the batch's own vouchers rather than off the
    package, because the package may have been repriced since.
    """
    return rows(
        "SELECT i.*, p.name AS package, "
        " COALESCE((SELECT v.price FROM vouchers v WHERE v.intake_id = i.id "
        "           LIMIT 1), p.price) AS price "
        "FROM intakes i JOIN packages p ON p.id = i.package_id "
        "ORDER BY i.received_on DESC, i.id DESC LIMIT ?", (limit,))


def assignments(agent_id: int | None = None, limit: int = 50) -> list[dict]:
    sql = ("SELECT a.*, p.name AS package, ag.name AS agent, "
           "ag.code AS agent_code "
           "FROM assignments a JOIN packages p ON p.id = a.package_id "
           "JOIN agents ag ON ag.id = a.agent_id ")
    params: tuple = ()
    if agent_id:
        sql += "WHERE a.agent_id = ? "
        params = (agent_id,)
    return rows(sql + "ORDER BY a.assigned_on DESC, a.id DESC LIMIT ?",
                (*params, limit))