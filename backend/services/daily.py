"""Balancing a day.

A Route Officer takes printed vouchers out, sells them, and comes back with
cash. Balancing her is one line per package she is carrying:

    opening   what she had when the day started
    top-up    vouchers handed to her during the day
    sold      vouchers gone
    closing   opening + top-up - sold - void
    expected  sold x price
    variance  cash handed in - expected

Only the cash is typed. Everything else is worked out.

Sold is counted two ways, in this order:

  1. If she has been marking cards on her own screen, that count is used. It
     is independent of the cash, so a variance means something: selling ten
     and paying for eight shows up as a shortage of two vouchers' worth.

  2. If she has marked nothing - a paper-only agent who never opens the app -
     sold is worked back out of the cash, cash divided by the price. Any
     remainder that does not make a whole voucher becomes the variance. With
     no independent count there is nothing to catch an underpayment, which is
     a limit of the method rather than of the software.

Nothing here charges anyone twice. A shortage is turned into a debt once, at
close, and tomorrow's opening is the stock she is actually holding.
"""

from __future__ import annotations

from backend.db import execute, one_dict, rows, scalar, transaction
from backend.money import today
from backend.services import agents as agent_service
from backend.services import ledger, stock

OPEN = "open"
CLOSED = "closed"


# --------------------------------------------------------------------------
# Pure arithmetic, so it can be checked without a database
# --------------------------------------------------------------------------

def line_figures(opening_qty: int, topup_qty: int, price: int, cash: int,
                 sold_qty: int | None = None, void_qty: int = 0) -> dict:
    """One package's line for one agent for one day."""
    price = max(0, int(price))
    cash = max(0, int(cash))
    counted = sold_qty is not None and sold_qty > 0

    if counted:
        sold = int(sold_qty)
    elif price > 0:
        sold = cash // price          # worked back out of the cash
    else:
        sold = 0

    expected = sold * price
    return {
        "price": price,
        "opening_qty": opening_qty,
        "topup_qty": topup_qty,
        "sold_qty": sold,
        "void_qty": void_qty,
        "closing_qty": opening_qty + topup_qty - sold - void_qty,
        "cash": cash,
        "expected": expected,
        "variance": cash - expected,
        "counted": counted,
        "opening": opening_qty * price,
        "topup": topup_qty * price,
        "closing": (opening_qty + topup_qty - sold - void_qty) * price,
    }


def roll_up(lines: list[dict], rate_bp: int = 0) -> dict:
    """Add the lines into the one row that appears on the daily table."""
    total = {
        "opening_qty": sum(l["opening_qty"] for l in lines),
        "topup_qty": sum(l["topup_qty"] for l in lines),
        "sold_qty": sum(l["sold_qty"] for l in lines),
        "closing_qty": sum(l["closing_qty"] for l in lines),
        "opening": sum(l["opening"] for l in lines),
        "topup": sum(l["topup"] for l in lines),
        "closing": sum(l["closing"] for l in lines),
        "collection": sum(l["cash"] for l in lines),
        "expected": sum(l["expected"] for l in lines),
        "counted": any(l["counted"] for l in lines),
    }
    total["variance"] = total["collection"] - total["expected"]
    total["shortage"] = max(0, -total["variance"])
    total["surplus"] = max(0, total["variance"])
    total["commission"] = ledger.commission_on(total["collection"], rate_bp)
    return total


# --------------------------------------------------------------------------
# Reading the day out of the records
# --------------------------------------------------------------------------

def _counts(agent_id: int, day: str, column: str) -> dict[tuple, int]:
    """Vouchers of hers that changed state today, by package and price."""
    where = {"sold": "status = 'sold' AND sold_on = ?",
             "void": "status = 'dead' AND sold_on = ?"}[column]
    return {(r["package_id"], r["price"]): r["qty"] for r in rows(
        f"SELECT package_id, price, COUNT(*) AS qty FROM vouchers "
        f"WHERE agent_id = ? AND {where} GROUP BY package_id, price",
        (agent_id, day))}


def _held(agent_id: int) -> dict[tuple, int]:
    return {(r["package_id"], r["price"]): r["qty"] for r in rows(
        "SELECT package_id, price, COUNT(*) AS qty FROM vouchers "
        "WHERE agent_id = ? AND status = 'agent' GROUP BY package_id, price",
        (agent_id,))}


def _topups(agent_id: int, day: str) -> dict[tuple, int]:
    return {(r["package_id"], r["price"]): r["qty"] for r in rows(
        "SELECT package_id, price, COUNT(*) AS qty FROM vouchers "
        "WHERE agent_id = ? AND assigned_on = ? GROUP BY package_id, price",
        (agent_id, day))}


def _saved_cash(agent_id: int, day: str) -> dict[tuple, int]:
    return {(r["package_id"], r["price"]): r["cash"] for r in rows(
        "SELECT package_id, price, cash FROM daily_lines "
        "WHERE agent_id = ? AND day = ?", (agent_id, day))}


def lines_for(agent_id: int, day: str, cash: dict | None = None) -> list[dict]:
    """Every package line for one agent on one day.

    Opening is never typed and never read off yesterday's row. It is worked
    backwards from the records: what she is holding now, plus what has gone
    today, less what she was given today, is what she started with. That is
    right on her very first day, when there is no previous row to read.
    """
    held = _held(agent_id)
    sold = _counts(agent_id, day, "sold")
    void = _counts(agent_id, day, "void")
    topup = _topups(agent_id, day)
    saved = _saved_cash(agent_id, day)
    typed = cash or {}

    meta = {r["id"]: r for r in rows(
        "SELECT id, name, colour, validity, sort_order FROM packages")}

    keys = set(held) | set(sold) | set(void) | set(topup) | set(saved)
    out = []
    for key in keys:
        package_id, price = key
        # What she is holding now, plus everything that left today, less
        # what she was given today, is what she started the day with.
        opening_qty = (held.get(key, 0) + sold.get(key, 0) + void.get(key, 0)
                       - topup.get(key, 0))
        money = typed.get(key, saved.get(key, 0))
        line = line_figures(
            opening_qty=max(0, opening_qty),
            topup_qty=topup.get(key, 0),
            price=price,
            cash=money,
            sold_qty=sold.get(key, 0) or None,
            void_qty=void.get(key, 0),
        )
        package = meta.get(package_id, {})
        line.update(package_id=package_id,
                    name=package.get("name", "Package"),
                    colour=package.get("colour", "#0b6e4f"),
                    validity=package.get("validity", ""),
                    sort_order=package.get("sort_order", 100),
                    held_now=held.get(key, 0))
        out.append(line)
    out.sort(key=lambda l: (l["sort_order"], l["price"]))
    return out


def get_row(agent_id: int, day: str) -> dict:
    """The whole day for one agent, saved or not."""
    saved = one_dict("SELECT * FROM daily_rows WHERE agent_id = ? AND day = ?",
                     (agent_id, day))
    agent = agent_service.get(agent_id) or {}
    rate = agent.get("commission_rate", 0)

    if saved and saved["status"] == CLOSED:
        saved["lines"] = rows(
            "SELECT l.*, p.name, p.colour FROM daily_lines l "
            "JOIN packages p ON p.id = l.package_id "
            "WHERE l.agent_id = ? AND l.day = ? ORDER BY p.sort_order, l.price",
            (agent_id, day))
        saved["saved"] = True
        saved["closed"] = True
        saved["surplus"] = max(0, saved["variance"])
        return saved

    lines = lines_for(agent_id, day)
    row = roll_up(lines, rate)
    row.update(agent_id=agent_id, day=day, lines=lines, status=OPEN,
               closed=False, saved=bool(saved),
               note=(saved["note"] if saved else ""),
               id=(saved["id"] if saved else None))
    return row


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def save_row(agent_id: int, day: str, cash: dict, note: str = "") -> dict:
    """Record the cash she handed in, package by package, and recalculate.

    `cash` is keyed by (package_id, price). Saving does not commit the day.
    """
    agent = agent_service.get(agent_id)
    if agent is None:
        raise ValueError("That agent does not exist.")
    existing = one_dict("SELECT * FROM daily_rows WHERE agent_id = ? AND day = ?",
                        (agent_id, day))
    if existing and existing["status"] == CLOSED:
        raise ValueError("That day is closed and cannot be changed.")
    for amount in cash.values():
        if amount < 0:
            raise ValueError("A collection cannot be negative.")

    lines = lines_for(agent_id, day, cash)
    total = roll_up(lines, agent["commission_rate"])

    with transaction():
        execute("DELETE FROM daily_lines WHERE agent_id = ? AND day = ?",
                (agent_id, day))
        for line in lines:
            execute(
                "INSERT INTO daily_lines (agent_id, day, package_id, price, "
                "opening_qty, topup_qty, cash, sold_qty, closing_qty, expected, "
                "variance) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (agent_id, day, line["package_id"], line["price"],
                 line["opening_qty"], line["topup_qty"], line["cash"],
                 line["sold_qty"], line["closing_qty"], line["expected"],
                 line["variance"]))
        execute(
            "INSERT INTO daily_rows (agent_id, day, opening, topup, sold, "
            "collection, closing, on_hand, shortage, commission, opening_qty, "
            "topup_qty, sold_qty, closing_qty, expected, variance, note, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open') "
            "ON CONFLICT(agent_id, day) DO UPDATE SET "
            " opening = excluded.opening, topup = excluded.topup, "
            " sold = excluded.sold, collection = excluded.collection, "
            " closing = excluded.closing, on_hand = excluded.on_hand, "
            " shortage = excluded.shortage, commission = excluded.commission, "
            " opening_qty = excluded.opening_qty, topup_qty = excluded.topup_qty, "
            " sold_qty = excluded.sold_qty, closing_qty = excluded.closing_qty, "
            " expected = excluded.expected, variance = excluded.variance, "
            " note = excluded.note",
            (agent_id, day, total["opening"], total["topup"], total["expected"],
             total["collection"], total["closing"], total["closing"],
             total["shortage"], total["commission"], total["opening_qty"],
             total["topup_qty"], total["sold_qty"], total["closing_qty"],
             total["expected"], total["variance"], note.strip()))
    return get_row(agent_id, day)


def close_row(agent_id: int, day: str, user_id: int | None = None) -> dict:
    """Commit the day: post any shortage as a debt, credit the commission.

    A surplus is never quietly used to cancel a shortage. Each is recorded on
    its own, because they are different events and netting them hides both.
    """
    saved = one_dict("SELECT * FROM daily_rows WHERE agent_id = ? AND day = ?",
                     (agent_id, day))
    if saved is None:
        raise ValueError("Save the day before closing it.")
    if saved["status"] == CLOSED:
        raise ValueError("That day is already closed.")

    with transaction():
        variance = saved["variance"]
        if variance < 0:
            ledger.post(agent_id, day, "shortage", -variance, "daily",
                        saved["id"], f"Short on {day}", user_id)
        elif variance > 0:
            ledger.post(agent_id, day, "adjustment", -variance, "daily",
                        saved["id"], f"Handed in more than sold on {day}",
                        user_id)
        if saved["commission"]:
            ledger.post(agent_id, day, "commission", -saved["commission"],
                        "daily", saved["id"], f"Commission on {day}", user_id)
        execute("UPDATE daily_rows SET status = 'closed', "
                "closed_at = datetime('now'), closed_by = ? WHERE id = ?",
                (user_id, saved["id"]))
    return one_dict("SELECT * FROM daily_rows WHERE id = ?", (saved["id"],))


def reopen_row(agent_id: int, day: str, user_id: int | None = None) -> None:
    """Undo a close by reversing exactly what it posted, so the account keeps
    a full history instead of losing entries."""
    saved = one_dict("SELECT * FROM daily_rows WHERE agent_id = ? AND day = ?",
                     (agent_id, day))
    if saved is None or saved["status"] != CLOSED:
        raise ValueError("That day is not closed.")
    with transaction():
        for entry in rows("SELECT * FROM ledger WHERE ref_type = 'daily' "
                          "AND ref_id = ?", (saved["id"],)):
            ledger.post(agent_id, day, entry["kind"], -entry["amount"],
                        "reopen", saved["id"],
                        f"Reversed when {day} was reopened", user_id)
        execute("UPDATE daily_rows SET status = 'open', closed_at = NULL, "
                "closed_by = NULL WHERE id = ?", (saved["id"],))


# --------------------------------------------------------------------------
# The daily table
# --------------------------------------------------------------------------

def board(day: str) -> list[dict]:
    """Every active agent's line for one day."""
    out = []
    for agent in agent_service.all_agents():
        row = get_row(agent["id"], day)
        position = ledger.position(agent["id"])
        out.append({**row, "agent": agent, "code": agent["code"],
                    "name": agent["name"], "station": agent["station"],
                    "debt": position["debt"],
                    "commission_due": position["commission_due"]})
    return out


def day_totals(day: str) -> dict:
    lines = board(day)
    return {
        "agents": len(lines),
        "opening_qty": sum(r["opening_qty"] for r in lines),
        "topup_qty": sum(r["topup_qty"] for r in lines),
        "sold_qty": sum(r["sold_qty"] for r in lines),
        "closing_qty": sum(r["closing_qty"] for r in lines),
        "opening": sum(r["opening"] for r in lines),
        "topup": sum(r["topup"] for r in lines),
        "closing": sum(r["closing"] for r in lines),
        "collection": sum(r["collection"] for r in lines),
        "expected": sum(r["expected"] for r in lines),
        "shortage": sum(r.get("shortage", 0) for r in lines),
        "surplus": sum(r.get("surplus", 0) for r in lines),
        "commission": sum(r["commission"] for r in lines),
        "closed": sum(1 for r in lines if r.get("closed")),
    }


def history(agent_id: int, limit: int = 60) -> list[dict]:
    return rows("SELECT * FROM daily_rows WHERE agent_id = ? "
                "ORDER BY day DESC LIMIT ?", (agent_id, limit))


def open_days_before(day: str) -> int:
    return scalar("SELECT COUNT(*) FROM daily_rows WHERE status = 'open' "
                  "AND day < ?", (day,))


# Kept so older callers keep working.
def opening_for(agent_id: int, day: str) -> int:
    return sum(l["opening"] for l in lines_for(agent_id, day))


def sold_on(agent_id: int, day: str) -> int:
    return scalar("SELECT COALESCE(SUM(price),0) FROM vouchers "
                  "WHERE agent_id = ? AND status = 'sold' AND sold_on = ?",
                  (agent_id, day))