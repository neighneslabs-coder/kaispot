"""Regular customers, so an agent does not retype the same details daily.

A number is optional. Plenty of an agent's regulars are simply people she
knows by name who come to the same spot every week, and refusing to save them
without a phone number would push her back to memory and scraps of paper.
When there is no number, the WhatsApp and Telegram buttons still work - they
just open the app's own contact picker instead of going straight to a chat.
"""

from __future__ import annotations

from backend.db import execute, one, one_dict, rows
from backend.money import clean_phone


def for_agent(agent_id: int) -> list[dict]:
    """Her customers, most recently served first."""
    return rows(
        "SELECT * FROM clients WHERE agent_id = ? "
        "ORDER BY CASE WHEN last_sold IS NULL THEN 1 ELSE 0 END, "
        "last_sold DESC, name COLLATE NOCASE",
        (agent_id,))


def get(client_id: int, agent_id: int) -> dict | None:
    return one_dict("SELECT * FROM clients WHERE id = ? AND agent_id = ?",
                    (client_id, agent_id))


def create(agent_id: int, name: str, phone: str = "", note: str = "") -> int:
    name = name.strip()
    if not name:
        raise ValueError("Give the customer a name.")

    number = clean_phone(phone)

    if number:
        clash = one("SELECT name FROM clients WHERE agent_id = ? AND phone = ?",
                    (agent_id, number))
        if clash:
            raise ValueError(f"That number is already saved as "
                             f"{clash['name']}.")
    else:
        # Without a number the name is all there is to tell two people apart,
        # so a repeat is almost certainly the same person entered twice.
        clash = one("SELECT id FROM clients WHERE agent_id = ? "
                    "AND name = ? COLLATE NOCASE AND phone = ''",
                    (agent_id, name))
        if clash:
            raise ValueError(f"{name} is already saved.")

    return execute(
        "INSERT INTO clients (agent_id, name, phone, note) VALUES (?,?,?,?)",
        (agent_id, name, number, note.strip()))


def update(client_id: int, agent_id: int, name: str, phone: str = "",
           note: str = "") -> None:
    client = get(client_id, agent_id)
    if client is None:
        raise ValueError("That customer is not on your list.")
    if not name.strip():
        raise ValueError("Give the customer a name.")

    number = clean_phone(phone)
    if number:
        clash = one("SELECT name FROM clients WHERE agent_id = ? AND phone = ? "
                    "AND id <> ?", (agent_id, number, client_id))
        if clash:
            raise ValueError(f"That number is already saved as "
                             f"{clash['name']}.")

    execute("UPDATE clients SET name = ?, phone = ?, note = ? WHERE id = ? "
            "AND agent_id = ?",
            (name.strip(), number, note.strip(), client_id, agent_id))


def delete(client_id: int, agent_id: int) -> None:
    execute("DELETE FROM clients WHERE id = ? AND agent_id = ?",
            (client_id, agent_id))