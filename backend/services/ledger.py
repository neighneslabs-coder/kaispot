"""One account per agent, holding both directions.

    positive amount = the agent owes KAISPOT
    negative amount = KAISPOT owes the agent

Because every balance on every screen is a SUM over this one table, recording
a payment moves the agent's debt, the office total, the dashboard and the
agent's own screen in the same instant. There is no second place to update
and therefore no way for them to disagree.
"""

from __future__ import annotations

from backend.db import execute, one, one_dict, rows, scalar

DEBT_KINDS = ("shortage", "payment", "writeoff", "adjustment")
PAY_KINDS = ("commission", "commission_paid")

LABELS = {
    "shortage": "Shortage",
    "payment": "Payment received",
    "writeoff": "Written off",
    "adjustment": "Adjustment",
    "commission": "Commission earned",
    "commission_paid": "Commission paid out",
}


def post(agent_id: int, entry_date: str, kind: str, amount: int,
         ref_type: str = "", ref_id: int | None = None, note: str = "",
         user_id: int | None = None) -> int:
    return execute(
        "INSERT INTO ledger (agent_id, entry_date, kind, amount, ref_type, "
        "ref_id, note, created_by) VALUES (?,?,?,?,?,?,?,?)",
        (agent_id, entry_date, kind, amount, ref_type, ref_id, note, user_id))


def commission_on(amount: int, rate_bp: int) -> int:
    """Commission is earned on cash actually received, never on sales, so
    handing in short earns proportionally less. Rounded down to the shilling."""
    if amount <= 0 or rate_bp <= 0:
        return 0
    return (amount * rate_bp) // 10_000


def record_payment(agent_id: int, entry_date: str, amount: int,
                   note: str = "", user_id: int | None = None) -> dict:
    """An agent clears part of a debt.

    Commission is earned on this too. The money reached KAISPOT in the end,
    and an agent who gets nothing for clearing a debt has no reason to clear
    it.
    """
    if amount <= 0:
        raise ValueError("Enter an amount greater than zero.")
    agent = one_dict("SELECT * FROM agents WHERE id = ?", (agent_id,))
    if agent is None:
        raise ValueError("That agent does not exist.")

    post(agent_id, entry_date, "payment", -amount, "payment", None,
         note.strip() or "Payment received", user_id)
    earned = commission_on(amount, agent["commission_rate"])
    if earned:
        post(agent_id, entry_date, "commission", -earned, "payment", None,
             "Commission on payment", user_id)
    return {"paid": amount, "commission": earned,
            "position": position(agent_id)}


def pay_commission(agent_id: int, entry_date: str, amount: int,
                   note: str = "", user_id: int | None = None) -> None:
    if amount <= 0:
        raise ValueError("Enter an amount greater than zero.")
    post(agent_id, entry_date, "commission_paid", amount, "payout", None,
         note.strip() or "Commission paid out", user_id)


def adjust(agent_id: int, entry_date: str, amount: int, note: str,
           user_id: int | None = None) -> None:
    """A signed correction. A reason is required: an unexplained change to
    somebody's money is how trust in a system dies."""
    if amount == 0:
        raise ValueError("An adjustment of zero does nothing.")
    if not note.strip():
        raise ValueError("Give a reason for the adjustment.")
    post(agent_id, entry_date, "adjustment", amount, "manual", None,
         note.strip(), user_id)


def write_off(agent_id: int, entry_date: str, amount: int, note: str,
              user_id: int | None = None) -> None:
    if amount <= 0:
        raise ValueError("Enter an amount greater than zero.")
    if not note.strip():
        raise ValueError("Say why this debt is being written off.")
    post(agent_id, entry_date, "writeoff", -amount, "manual", None,
         note.strip(), user_id)


def _sum(agent_id: int, kinds: tuple[str, ...]) -> int:
    marks = ",".join("?" for _ in kinds)
    return scalar(f"SELECT COALESCE(SUM(amount),0) FROM ledger "
                  f"WHERE agent_id = ? AND kind IN ({marks})",
                  (agent_id, *kinds))


def position(agent_id: int) -> dict:
    debt = _sum(agent_id, DEBT_KINDS)
    commission_due = -_sum(agent_id, PAY_KINDS)
    return {"debt": debt, "commission_due": commission_due,
            "net": debt - commission_due}


def entries(agent_id: int, limit: int = 200) -> list[dict]:
    return rows("SELECT * FROM ledger WHERE agent_id = ? "
                "ORDER BY entry_date DESC, id DESC LIMIT ?", (agent_id, limit))


def all_positions(include_inactive: bool = False) -> list[dict]:
    marks_debt = ",".join("?" for _ in DEBT_KINDS)
    marks_pay = ",".join("?" for _ in PAY_KINDS)
    where = "" if include_inactive else "WHERE a.active = 1 "
    data = rows(
        f"SELECT a.id, a.code, a.name, a.station, a.phone, a.commission_rate, "
        f" a.credit_limit, a.active, "
        f" COALESCE((SELECT SUM(amount) FROM ledger l WHERE l.agent_id = a.id "
        f"   AND l.kind IN ({marks_debt})),0) AS debt, "
        f" -COALESCE((SELECT SUM(amount) FROM ledger l WHERE l.agent_id = a.id "
        f"   AND l.kind IN ({marks_pay})),0) AS commission_due "
        f"FROM agents a {where}ORDER BY a.name",
        (*DEBT_KINDS, *PAY_KINDS))
    for row in data:
        row["net"] = row["debt"] - row["commission_due"]
        row["over_limit"] = bool(row["credit_limit"]
                                 and row["debt"] > row["credit_limit"])
    return data


def company_totals() -> dict:
    positions = all_positions()
    return {
        "debt": sum(p["debt"] for p in positions if p["debt"] > 0),
        "commission_due": sum(p["commission_due"] for p in positions
                              if p["commission_due"] > 0),
        "agents_in_debt": sum(1 for p in positions if p["debt"] > 0),
        "over_limit": sum(1 for p in positions if p["over_limit"]),
    }
