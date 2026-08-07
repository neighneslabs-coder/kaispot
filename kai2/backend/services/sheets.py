"""PDF voucher sheets the office puts in front of one named agent.

A sheet carries live voucher numbers, so it belongs to exactly one agent.
There is no "everybody" sheet: that would let any agent read and sell another
agent's stock. A sheet with no owner is treated as nobody's and is shown to
the office only, with a prompt to give it an owner.
"""

from __future__ import annotations

import pathlib
import re
import secrets

from backend.config import UPLOAD_DIR
from backend.db import execute, one_dict, rows

ALLOWED = {".pdf"}
MAX_TITLE = 120


def page_count(path: pathlib.Path) -> int:
    """How many pages a PDF has.

    pypdf reads the file's structure, not its text, so this works even on a
    sheet that was printed to PDF as a picture and has no text in it at all.
    """
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages)
    except Exception:
        return 0


def store(upload, title: str, agent_id: int | None, intake_id: int | None = None,
          user_id: int | None = None) -> int:
    """Save an uploaded file under a name of our own choosing.

    The original filename is kept only for display. What lands on disk is a
    random name, so a file called ../../etc/passwd cannot escape the folder.
    """
    original = pathlib.Path(upload.filename or "sheet.pdf").name
    suffix = pathlib.Path(original).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError("Only PDF files can be uploaded.")
    if not agent_id:
        raise ValueError("Choose which agent this sheet belongs to.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{secrets.token_hex(12)}{suffix}"
    target = UPLOAD_DIR / stored
    upload.save(target)

    sheet_id = execute(
        "INSERT INTO sheets (title, agent_id, intake_id, filename, stored_name, "
        "size, uploaded_by) VALUES (?,?,?,?,?,?,?)",
        ((title.strip() or original)[:MAX_TITLE], agent_id, intake_id, original,
         stored, target.stat().st_size, user_id))
    return sheet_id


def card_codes(prefix: str, count: int, start: int = 1) -> list[str]:
    """Build one code per card on a printed sheet.

    The cards on a Mikhmon sheet are numbered [1], [2], [3] and so on, and
    that number is printed on the paper in the agent's hand. Using it as the
    code means she can find the card on screen without anybody having to read
    the username off the page - which is what makes this work on a sheet that
    was printed to PDF as a picture.

    The real usernames stay on the paper, where she reads them to the
    customer. What the system needs is a way to say "this card is gone", and
    the printed number does that exactly.
    """
    prefix = (prefix or "").strip().upper().replace(" ", "-")
    if not prefix:
        raise ValueError("Give the sheet a short code, such as PLAT-0805.")
    if count < 1:
        raise ValueError("Say how many voucher cards are on the sheet.")
    if count > 2000:
        raise ValueError("That is more than 2,000 cards. Split the sheet up.")
    width = max(3, len(str(start + count - 1)))
    return [f"{prefix}-{n:0{width}d}" for n in range(start, start + count)]


def parse_pairs(raw: str) -> list[dict]:
    """Read usernames and passwords copied out of Mikhmon.

    One card per line. The first thing on the line is the username, the
    second - if there is one - is the password. Commas, tabs, pipes and the
    brackets Mikhmon prints round the card number are all ignored, so text
    pasted straight off the screen works without tidying.

        Srpn 474
        [2] Ssvh 363
        Svde,385
        Saur

    Returns the cards in the order they were pasted, which is the order they
    are printed on the sheet.
    """
    entries: list[dict] = []
    seen: set[str] = set()
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # Drop a leading card number such as "[12]" or "12." - it is the
        # position on the sheet, not part of the code.
        line = re.sub(r"^\[?\s*\d{1,4}\s*[\].:)-]\s*", "", line)
        parts = [p for p in re.split(r"[\s,;|\t]+", line) if p]
        if not parts:
            continue
        code = parts[0].strip()
        secret = parts[1].strip() if len(parts) > 1 else ""
        if not code or code.lower() in seen:
            continue
        seen.add(code.lower())
        entries.append({"code": code, "secret": secret})
    return entries


def suggest_code(agent_code: str, day: str) -> str:
    """A sheet code that will not collide with another agent's.

    Every card on a sheet carries this, and the codes have to be unique
    across the whole system. Building it from the agent and the date means
    two agents given sheets on the same day never clash, which is the one
    thing that silently gave an agent an empty screen.
    """
    stamp = str(day).replace("-", "")[2:]
    return f"{(agent_code or 'SH').upper().replace(' ', '')}-{stamp}"


PAIR = re.compile(
    r"([A-Za-z][A-Za-z0-9]{2,19})"          # the username
    r"\s*[\s,;/|:\t-]\s*"                    # whatever separates them
    r"([A-Za-z0-9]{2,19})")                 # the password

SKIP = {"username", "password", "user", "pass", "profile", "price", "code",
        "voucher", "comment", "time", "limit", "valid", "validity", "no",
        "server", "hotspot", "kaispot"}


def read_pairs(raw: str) -> list[dict]:
    """Pull username and password pairs out of pasted text.

    Mikhmon lists the vouchers on a web page. Selecting that list and copying
    it gives lines that vary wildly depending on the browser and how much of
    the table was caught - so this is deliberately loose about separators and
    ignores the heading words that come along for the ride.

    A line with only one word is taken as a username with no password.
    """
    found: list[dict] = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        cleaned = line.strip("|").strip()
        words = [w for w in re.split(r"[\s,;/|:\t]+", cleaned) if w]
        words = [w for w in words if w.lower() not in SKIP]
        if not words:
            continue
        if len(words) >= 2:
            found.append({"username": words[0], "secret": words[1]})
        else:
            found.append({"username": words[0], "secret": ""})
    return found


def fill_credentials(intake_id: int, pairs: list[dict]) -> dict:
    """Write pasted usernames and passwords onto a sheet's cards, in order.

    Card 1 gets the first pair, card 2 the second, and so on - which is the
    order Mikhmon prints them and the order they were registered in. Nothing
    else about the card changes: its number, its price and whether it has
    been sold all stay exactly as they were.
    """
    cards = rows("SELECT id, code FROM vouchers WHERE intake_id = ? "
                 "ORDER BY id", (intake_id,))
    if not cards:
        raise ValueError("That sheet has no cards registered against it.")
    if not pairs:
        raise ValueError("No usernames were found in what you pasted.")

    filled = 0
    for card, pair in zip(cards, pairs):
        execute("UPDATE vouchers SET username = ?, secret = ? WHERE id = ?",
                (pair["username"], pair["secret"], card["id"]))
        filled += 1

    return {
        "cards": len(cards),
        "pasted": len(pairs),
        "filled": filled,
        "short": max(0, len(cards) - len(pairs)),
        "spare": max(0, len(pairs) - len(cards)),
        "first": [{"code": c["code"], **p} for c, p in
                  zip(cards[:5], pairs[:5])],
    }


def cards_of(intake_id: int) -> list[dict]:
    return rows("SELECT id, code, username, secret, status FROM vouchers "
                "WHERE intake_id = ? ORDER BY id", (intake_id,))


def attach_intake(sheet_id: int, intake_id: int) -> None:
    execute("UPDATE sheets SET intake_id = ? WHERE id = ?", (intake_id, sheet_id))


def visible_to(agent_id: int | None) -> list[dict]:
    """What an agent may open, or - with no agent - the office's full list.

    Deliberately the same rule the open route enforces. When these two drifted
    apart, an agent saw a sheet listed on her screen and was told it was not
    hers when she tapped it.
    """
    if agent_id is None:
        return rows(
            "SELECT s.*, a.name AS agent_name, a.code AS agent_code "
            "FROM sheets s LEFT JOIN agents a ON a.id = s.agent_id "
            "ORDER BY s.uploaded_at DESC")
    return rows("SELECT * FROM sheets WHERE agent_id = ? "
                "ORDER BY uploaded_at DESC", (agent_id,))


def unassigned() -> list[dict]:
    """Sheets with no owner. Nobody can open these until one is given."""
    return rows("SELECT * FROM sheets WHERE agent_id IS NULL "
                "ORDER BY uploaded_at DESC")


def get(sheet_id: int) -> dict | None:
    return one_dict("SELECT * FROM sheets WHERE id = ?", (sheet_id,))


def may_open(sheet: dict, user) -> bool:
    """One place that decides who can open a sheet."""
    if sheet is None:
        return False
    if user["role"] in ("admin", "manager"):
        return True
    return bool(sheet["agent_id"]) and sheet["agent_id"] == user["agent_id"]


def assign(sheet_id: int, agent_id: int) -> None:
    """Give an existing sheet an owner, or move it to a different agent."""
    if not agent_id:
        raise ValueError("Choose which agent this sheet belongs to.")
    if get(sheet_id) is None:
        raise ValueError("That sheet is not there.")
    execute("UPDATE sheets SET agent_id = ? WHERE id = ?", (agent_id, sheet_id))


def path_of(sheet: dict) -> pathlib.Path:
    return UPLOAD_DIR / sheet["stored_name"]


def delete(sheet_id: int) -> None:
    sheet = get(sheet_id)
    if sheet is None:
        return
    try:
        path_of(sheet).unlink(missing_ok=True)
    except OSError:
        pass
    execute("DELETE FROM sheets WHERE id = ?", (sheet_id,))