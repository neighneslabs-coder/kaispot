"""The agent's point of sale, and getting the voucher to the customer.

Selling takes the next unsold voucher out of the agent's own stock, marks it
sold, and hands back ready-made share links. Nothing is sent by KAISPOT
itself: the links open WhatsApp, Telegram or the SMS app already on the
agent's phone, with the message written out. That needs no paid gateway, no
API keys and no internet on the server - which matters, because this server
sits behind the MikroTik walled garden with no route to the outside.
"""

from __future__ import annotations

import urllib.parse

from backend.db import execute, one_dict, rows, scalar, transaction
from backend.money import clean_phone, today
from backend.services import agents as agent_service

CHANNELS = ("whatsapp", "telegram", "sms", "counter")


def stamp_now() -> str:
    """When the voucher was handed over, in words a customer reads easily.

    Worth sending: it settles a later argument about when a voucher was
    bought, and it lets the customer see the message is today's rather than
    an old one forwarded on.
    """
    from datetime import datetime
    return datetime.now().strftime("%d %b %Y at %I:%M %p").replace(" 0", " ")


def message_for(voucher: dict, package: dict, company: str = "KAISPOT",
                stamp: str | None = None) -> str:
    """The text the customer receives.

    When the real username off the printed card is known, that is what gets
    sent, because it is what the customer types into the hotspot. Until the
    codes have been filled in, the card's own number goes instead, which the
    agent can at least read out beside the paper in her hand.
    """
    lines = [f"{company} internet voucher", ""]
    lines.append(f"Package: {package['name']}")
    if package.get("validity"):
        lines.append(f"Valid: {package['validity']}")
    # The price frozen on this card, not whatever the package costs today.
    # A card printed at 1,000 is worth 1,000 to the customer holding it, even
    # after the package has been repriced.
    lines.append(f"Price: UGX {int(voucher.get('price') or package['price']):,}")
    lines.append("")

    username = str(voucher.get("username") or "").strip()
    if username:
        lines.append(f"Username: {username}")
        if voucher.get("secret"):
            lines.append(f"Password: {voucher['secret']}")
        lines.append(f"Card: {voucher['code']}")
    else:
        lines.append(f"Code: {voucher['code']}")
        if voucher.get("secret"):
            lines.append(f"Password: {voucher['secret']}")

    lines.append("")
    lines.append("Connect to the WiFi, open the login page and enter it.")
    lines.append("")
    lines.append(f"Sold {stamp or stamp_now()}")
    return "\n".join(lines)


def share_links(message: str, phone: str = "") -> dict:
    """Links that open the messaging apps with the voucher already typed."""
    text = urllib.parse.quote(message)
    number = clean_phone(phone)
    return {
        "whatsapp": (f"https://wa.me/{number}?text={text}" if number
                     else f"https://wa.me/?text={text}"),
        "telegram": f"https://t.me/share/url?url=&text={text}",
        "sms": (f"sms:{'+' + number if number else ''}?body={text}"),
        "copy": message,
        "phone": number,
    }


def available(agent_id: int, package_id: int) -> int:
    return scalar("SELECT COUNT(*) FROM vouchers WHERE agent_id = ? AND "
                  "package_id = ? AND status = 'agent'", (agent_id, package_id))


def sell(agent_id: int, package_id: int, channel: str = "counter",
         client_phone: str = "", client_id: int | None = None,
         user_id: int | None = None, day: str | None = None) -> dict:
    """Sell one voucher from the agent's own stock."""
    day = day or today()
    agent = agent_service.get(agent_id)
    if agent is None:
        raise ValueError("That agent does not exist.")
    if channel not in CHANNELS:
        channel = "counter"

    with transaction():
        voucher = one_dict(
            "SELECT v.*, p.name AS package_name, p.validity, p.price AS pack_price "
            "FROM vouchers v JOIN packages p ON p.id = v.package_id "
            "WHERE v.agent_id = ? AND v.package_id = ? AND v.status = 'agent' "
            "ORDER BY v.id LIMIT 1", (agent_id, package_id))
        if voucher is None:
            raise ValueError(
                "There are no vouchers of that package left in your stock. "
                "Ask the office for a top-up.")
        execute(
            "UPDATE vouchers SET status = 'sold', sold_on = ?, "
            "sold_at = datetime('now'), client_phone = ?, channel = ?, "
            "sold_by = ? WHERE id = ?",
            (day, clean_phone(client_phone), channel, user_id, voucher["id"]))
        if client_id:
            execute("UPDATE clients SET last_sold = ? WHERE id = ? AND agent_id = ?",
                    (day, client_id, agent_id))

    package = one_dict("SELECT * FROM packages WHERE id = ?", (package_id,))
    message = message_for(voucher, package)
    return {
        "voucher": voucher,
        "package": package,
        "message": message,
        "links": share_links(message, client_phone),
        "remaining": available(agent_id, package_id),
    }


def sell_voucher(voucher_id: int, agent_id: int, channel: str = "counter",
                 client_phone: str = "", client_id: int | None = None,
                 user_id: int | None = None, day: str | None = None) -> dict:
    """Sell one particular voucher, chosen off the sheet by the agent.

    This is how a paper sheet is worked: she reads the card in front of her,
    finds it on screen and marks it. Selling the *next* voucher in the pile is
    the wrong model for paper, because the customer may pick any card on the
    page.
    """
    day = day or today()
    voucher = one_dict(
        "SELECT v.*, p.name AS package_name, p.validity "
        "FROM vouchers v JOIN packages p ON p.id = v.package_id "
        "WHERE v.id = ? AND v.agent_id = ?", (voucher_id, agent_id))
    if voucher is None:
        raise ValueError("That voucher is not in your stock.")
    if voucher["status"] == "sold":
        raise ValueError(f"{voucher['code']} is already marked sold.")
    if voucher["status"] == "dead":
        raise ValueError(f"{voucher['code']} is marked invalid.")
    if channel not in CHANNELS:
        channel = "counter"

    with transaction():
        execute(
            "UPDATE vouchers SET status = 'sold', sold_on = ?, "
            "sold_at = datetime('now'), client_phone = ?, channel = ?, "
            "sold_by = ? WHERE id = ?",
            (day, clean_phone(client_phone), channel, user_id, voucher_id))
        if client_id:
            execute("UPDATE clients SET last_sold = ? WHERE id = ? "
                    "AND agent_id = ?", (day, client_id, agent_id))

    package = one_dict("SELECT * FROM packages WHERE id = ?",
                       (voucher["package_id"],))
    message = message_for(voucher, package)
    return {"voucher": voucher, "package": package, "message": message,
            "links": share_links(message, client_phone)}


def mark_invalid(voucher_id: int, agent_id: int, note: str = "",
                 day: str | None = None) -> dict:
    """A card that cannot be sold - torn, misprinted, already used.

    It leaves her stock, so it stops counting towards what she owes. A sold
    voucher cannot be made invalid: that would quietly erase a real sale.
    """
    voucher = one_dict("SELECT * FROM vouchers WHERE id = ? AND agent_id = ?",
                       (voucher_id, agent_id))
    if voucher is None:
        raise ValueError("That voucher is not in your stock.")
    if voucher["status"] == "sold":
        raise ValueError(
            f"{voucher['code']} is already sold. Undo the sale first if it "
            f"was a mistake.")
    # The date is stored so the day's balance can subtract it. Without it, a
    # card voided today would still be counted as stock she is holding.
    execute("UPDATE vouchers SET status = 'dead', sold_on = ? WHERE id = ?",
            (day or today(), voucher_id))
    return voucher


def restore_voucher(voucher_id: int, agent_id: int) -> dict:
    """Put an invalid card back into her stock."""
    voucher = one_dict("SELECT * FROM vouchers WHERE id = ? AND agent_id = ? "
                       "AND status = 'dead'", (voucher_id, agent_id))
    if voucher is None:
        raise ValueError("That voucher is not marked invalid.")
    execute("UPDATE vouchers SET status = 'agent', sold_on = NULL WHERE id = ?",
            (voucher_id,))
    return voucher


def sheet_for(agent_id: int) -> list[dict]:
    """Every card she is holding, in printed order, grouped by package.

    Sold and invalid cards stay on the list for the rest of the day so she can
    see what she has done and undo a mistake, which is what a paper sheet in
    her hand looks like.
    """
    groups: dict[tuple, dict] = {}
    for row in rows(
        "SELECT v.id, v.code, v.username, v.secret, v.price, v.status, v.sold_on, "
        " v.client_phone, v.channel, p.id AS package_id, p.name AS package_name, "
        " p.colour, p.validity "
        "FROM vouchers v JOIN packages p ON p.id = v.package_id "
        "WHERE v.agent_id = ? AND (v.status IN ('agent','dead') "
        "   OR (v.status = 'sold' AND v.sold_on = ?)) "
        "ORDER BY p.sort_order, v.price, v.id", (agent_id, today())):
        key = (row["package_id"], row["price"])
        if key not in groups:
            groups[key] = {
                "package_id": row["package_id"], "name": row["package_name"],
                "colour": row["colour"], "validity": row["validity"],
                "price": row["price"], "cards": [],
                "left": 0, "sold": 0, "invalid": 0,
            }
        group = groups[key]
        group["cards"].append(row)
        if row["status"] == "agent":
            group["left"] += 1
        elif row["status"] == "sold":
            group["sold"] += 1
        else:
            group["invalid"] += 1
    for group in groups.values():
        group["value_left"] = group["left"] * group["price"]
        group["value_sold"] = group["sold"] * group["price"]
    return list(groups.values())


def totals_for(agent_id: int, day: str | None = None) -> dict:
    """The running figures at the top of her screen."""
    day = day or today()
    sold = one_dict(
        "SELECT COUNT(*) AS qty, COALESCE(SUM(price),0) AS value FROM vouchers "
        "WHERE agent_id = ? AND status = 'sold' AND sold_on = ?",
        (agent_id, day)) or {"qty": 0, "value": 0}
    left = one_dict(
        "SELECT COUNT(*) AS qty, COALESCE(SUM(price),0) AS value FROM vouchers "
        "WHERE agent_id = ? AND status = 'agent'", (agent_id,)) or {"qty": 0, "value": 0}
    invalid = scalar("SELECT COUNT(*) FROM vouchers WHERE agent_id = ? "
                     "AND status = 'dead'", (agent_id,))
    return {"sold_qty": sold["qty"], "sold_value": sold["value"],
            "left_qty": left["qty"], "left_value": left["value"],
            "invalid_qty": invalid}


def void_sale(voucher_id: int, agent_id: int) -> None:
    """Put a voucher back if it was sold by mistake and never handed over."""
    voucher = one_dict("SELECT * FROM vouchers WHERE id = ? AND agent_id = ?",
                       (voucher_id, agent_id))
    if voucher is None:
        raise ValueError("That voucher is not yours.")
    if voucher["status"] != "sold":
        raise ValueError("That voucher is not marked as sold.")
    if voucher["sold_on"] != today():
        raise ValueError(
            "Only a sale made today can be undone. Ask the office to correct "
            "an older one.")
    execute("UPDATE vouchers SET status = 'agent', sold_on = NULL, "
            "sold_at = NULL, client_phone = '', channel = '' WHERE id = ?",
            (voucher_id,))


def recent_sales(agent_id: int, limit: int = 25) -> list[dict]:
    return rows(
        "SELECT v.*, p.name AS package_name FROM vouchers v "
        "JOIN packages p ON p.id = v.package_id "
        "WHERE v.agent_id = ? AND v.status = 'sold' "
        "ORDER BY v.sold_at DESC LIMIT ?", (agent_id, limit))


def sales_on(agent_id: int, day: str) -> dict:
    row = one_dict(
        "SELECT COUNT(*) AS qty, COALESCE(SUM(price),0) AS value FROM vouchers "
        "WHERE agent_id = ? AND status = 'sold' AND sold_on = ?", (agent_id, day))
    return row or {"qty": 0, "value": 0}


def by_channel(start: str, end: str) -> list[dict]:
    return rows(
        "SELECT COALESCE(NULLIF(channel,''),'counter') AS channel, "
        "COUNT(*) AS qty, COALESCE(SUM(price),0) AS value FROM vouchers "
        "WHERE status = 'sold' AND sold_on BETWEEN ? AND ? "
        "GROUP BY 1 ORDER BY value DESC", (start, end))