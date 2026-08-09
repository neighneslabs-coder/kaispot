"""Deleting things, and starting afresh.

Everything here destroys data on purpose, so three rules hold throughout:

  1. A backup is written before anything is removed. It lands in backups/ and
     is named with the date and time, so a mistake is always recoverable.
  2. Nothing runs without the caller passing the exact confirmation phrase.
  3. Every removal is written to the audit log before it happens, because
     afterwards there may be nothing left to explain it.

The gentler option is always offered first. Switching an agent off keeps every
figure they ever produced; deleting them does not.
"""

from __future__ import annotations

from backend.db import (backup, execute, get_db, one_dict, rows, scalar,
                        transaction)

# What an agent leaves behind, in the order it has to be cleared.
AGENT_TABLES = ("daily_lines", "daily_rows", "ledger", "clients", "sheets",
                "assignments")

DELETE_MODES = ("deactivate", "agent_only", "everything")

RESET_TABLES = [
    ("daily_lines", "Daily package lines"),
    ("daily_rows", "Daily balances"),
    ("ledger", "Debts and commission"),
    ("assignments", "Stock handed out"),
    ("vouchers", "Vouchers"),
    ("intakes", "Batches registered"),
    ("sheets", "Uploaded sheets"),
    ("clients", "Customers"),
    ("agents", "Sales agents"),
    ("buildings", "Buildings"),
    ("packages", "Packages"),
    ("audit_log", "Activity log"),
]


def _detach_users(user_ids: list[int]) -> None:
    """Let go of every reference to these sign-ins before deleting them.

    A sign-in gets stamped on things all over the system: who sold a voucher,
    who closed a day, who posted to a ledger, and every line of the activity
    log. Those references are what a database means by "in use", and deleting
    the row underneath them is refused.

    The stamps are cleared rather than the records destroyed. What happened
    still happened; it simply stops naming an account that no longer exists.
    The alternative - deleting the activity log along with the person - would
    erase the very trail that explains why they were removed.
    """
    if not user_ids:
        return
    marks = ",".join("?" for _ in user_ids)
    conn = get_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")]
    for table in tables:
        if table in ("users", "sqlite_sequence"):
            continue
        for ref in conn.execute(f"PRAGMA foreign_key_list({table})"):
            # ref: (id, seq, table, from, to, on_update, on_delete, match)
            if ref[2] != "users":
                continue
            column = ref[3]
            conn.execute(
                f"UPDATE {table} SET {column} = NULL "
                f"WHERE {column} IN ({marks})", tuple(user_ids))


def agent_footprint(agent_id: int) -> dict:
    """Exactly what would go, so the warning can be specific rather than vague."""
    counts = {
        "days": scalar("SELECT COUNT(*) FROM daily_rows WHERE agent_id = ?",
                       (agent_id,)),
        "ledger": scalar("SELECT COUNT(*) FROM ledger WHERE agent_id = ?",
                         (agent_id,)),
        "clients": scalar("SELECT COUNT(*) FROM clients WHERE agent_id = ?",
                          (agent_id,)),
        "sheets": scalar("SELECT COUNT(*) FROM sheets WHERE agent_id = ?",
                         (agent_id,)),
        "holding": scalar("SELECT COUNT(*) FROM vouchers WHERE agent_id = ? "
                          "AND status = 'agent'", (agent_id,)),
        "sold": scalar("SELECT COUNT(*) FROM vouchers WHERE agent_id = ? "
                       "AND status = 'sold'", (agent_id,)),
        "collected": scalar("SELECT COALESCE(SUM(collection),0) FROM daily_rows "
                            "WHERE agent_id = ?", (agent_id,)),
    }
    counts["holding_value"] = scalar(
        "SELECT COALESCE(SUM(price),0) FROM vouchers WHERE agent_id = ? "
        "AND status = 'agent'", (agent_id,))
    counts["traded"] = bool(counts["days"] or counts["ledger"]
                            or counts["sold"] or counts["holding"])
    return counts


def delete_agent(agent_id: int, mode: str, confirm: str,
                 user_id: int | None = None) -> dict:
    """Remove an agent, one of three ways.

      deactivate    keeps everything; they simply cannot trade or sign in
      agent_only    removes the person and their records, but the vouchers
                    survive: unsold ones go back on the office shelf and sold
                    ones stay on record, just without an owner
      everything    removes the person and every voucher ever issued to them,
                    sold ones included. Stock totals will fall.

    `confirm` must be the agent's code. Asking for something specific rather
    than a yes/no is what stops the wrong row being wiped by a stray click.
    """
    if mode not in DELETE_MODES:
        raise ValueError("Choose what should happen to the agent's records.")
    agent = one_dict("SELECT * FROM agents WHERE id = ?", (agent_id,))
    if agent is None:
        raise ValueError("That agent does not exist.")

    if mode != "deactivate":
        if confirm.strip().upper() != agent["code"].strip().upper():
            raise ValueError(
                f"Type {agent['code']} in the confirmation box to delete "
                f"{agent['name']}. Nothing has been changed.")

    footprint = agent_footprint(agent_id)

    if mode == "deactivate":
        execute("UPDATE agents SET active = 0 WHERE id = ?", (agent_id,))
        execute("UPDATE users SET active = 0 WHERE agent_id = ?", (agent_id,))
        return {"mode": mode, "agent": agent, "footprint": footprint,
                "backup": None}

    saved = backup()
    execute("INSERT INTO audit_log (user_id, action, detail) VALUES (?,?,?)",
            (user_id, f"agent-delete-{mode}",
             f"{agent['code']} {agent['name']}: {footprint['days']} days, "
             f"{footprint['sold']} sold, {footprint['holding']} held. "
             f"Backup {saved}"))

    with transaction():
        if mode == "everything":
            execute("DELETE FROM vouchers WHERE agent_id = ?", (agent_id,))
        else:
            # Unsold cards return to the shelf; sold ones keep their history.
            execute("UPDATE vouchers SET status = 'office', agent_id = NULL, "
                    "assigned_on = NULL WHERE agent_id = ? AND status = 'agent'",
                    (agent_id,))
            execute("UPDATE vouchers SET agent_id = NULL WHERE agent_id = ?",
                    (agent_id,))
        for table in AGENT_TABLES:
            execute(f"DELETE FROM {table} WHERE agent_id = ?", (agent_id,))
        sign_ins = [r["id"] for r in rows(
            "SELECT id FROM users WHERE agent_id = ?", (agent_id,))]
        _detach_users(sign_ins)
        execute("DELETE FROM users WHERE agent_id = ?", (agent_id,))
        execute("DELETE FROM agents WHERE id = ?", (agent_id,))

    return {"mode": mode, "agent": agent, "footprint": footprint,
            "backup": saved}


def what_is_there() -> list[dict]:
    """How much of everything exists, for the reset screen."""
    out = []
    for table, label in RESET_TABLES:
        try:
            out.append({"table": table, "label": label,
                        "count": scalar(f"SELECT COUNT(*) FROM {table}")})
        except Exception:
            out.append({"table": table, "label": label, "count": 0})
    return out


def reset(keep_packages: bool, keep_buildings: bool, keep_users: bool,
          confirm: str, user_id: int | None = None) -> dict:
    """Empty the app so it can be started again from scratch.

    Sign-ins are kept unless told otherwise, because wiping them locks
    everybody out of the machine including whoever pressed the button. The
    administrator account is never removed.
    """
    if confirm.strip().upper() != "ERASE":
        raise ValueError(
            "Type ERASE in the confirmation box to empty the app. Nothing has "
            "been changed.")

    saved = backup()
    before = {row["table"]: row["count"] for row in what_is_there()}
    execute("INSERT INTO audit_log (user_id, action, detail) VALUES (?,?,?)",
            (user_id, "app-reset",
             f"Backup {saved}. Removed: "
             + ", ".join(f"{k} {v}" for k, v in before.items() if v)))

    skip = set()
    if keep_packages:
        skip.add("packages")
    if keep_buildings:
        skip.add("buildings")
    if keep_users:
        skip.add("agents")          # agent sign-ins need their agent rows

    with transaction():
        # Sign-ins go first, and only after every reference to them has been
        # released. Clearing the agents table would otherwise cascade into
        # their sign-ins while the activity log still points at them, and the
        # database refuses that - which is what a foreign key is for.
        # Keeping the agents means keeping the sign-ins that belong to them,
        # otherwise they are kept but locked out.
        going = [] if keep_users else [r["id"] for r in rows(
            "SELECT id FROM users WHERE role <> 'admin'")]
        if going:
            _detach_users(going)
            marks = ",".join("?" for _ in going)
            execute(f"DELETE FROM users WHERE id IN ({marks})", tuple(going))

        for table, _label in RESET_TABLES:
            if table in skip or table == "audit_log":
                continue
            execute(f"DELETE FROM {table}")

        execute("UPDATE users SET agent_id = NULL WHERE agent_id IS NOT NULL "
                "AND agent_id NOT IN (SELECT id FROM agents)")
        # Let the numbering start again so the fresh app looks fresh.
        for table, _label in RESET_TABLES:
            if table not in skip:
                execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))

    return {"backup": saved, "before": before,
            "kept": sorted(skip) or ["nothing"]}