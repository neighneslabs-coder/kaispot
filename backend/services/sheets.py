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

# =========================================================================
# Reading a printed sheet
#
# A sheet printed through "Microsoft Print to PDF" holds no text and no
# image. Every letter is traced as a vector outline, so nothing can simply
# read it out. But the same letter is always drawn with the same outline, so
# the shapes can be fingerprinted and grouped, and the sheet then teaches
# itself: each card prints its own number, [1] to [100], which names every
# digit shape without anybody typing a thing.
#
# Letters that never appear in a word the sheet already spells have to be
# named once, by hand. After that the alphabet is remembered and every later
# sheet from the same generator reads on its own.
# =========================================================================

import json
import re as _re

TOKEN = _re.compile(rb"(-?\d+\.?\d*)|([A-Za-z'\"*]+)")
GRID_W, GRID_H = 10, 14
MATCH = 0.80
ALPHABET_KEY = "sheet_alphabet"


def _mul(a, b):
    return (a[0]*b[0] + a[1]*b[2], a[0]*b[1] + a[1]*b[3],
            a[2]*b[0] + a[3]*b[2], a[2]*b[1] + a[3]*b[3],
            a[4]*b[0] + a[5]*b[2] + b[4], a[4]*b[1] + a[5]*b[3] + b[5])


def _at(m, x, y):
    return (m[0]*x + m[2]*y + m[4], m[1]*x + m[3]*y + m[5])


def _shapes(data: bytes) -> list:
    """Every filled shape on a page, in page coordinates.

    A small interpreter for the drawing commands: it tracks the transform
    stack, because a shape's position means nothing without the transform in
    force when it was drawn.
    """
    ctm = (1, 0, 0, 1, 0, 0)
    stack, operands = [], []
    cur, subs, out = [], [], []
    for match in TOKEN.finditer(data):
        number, word = match.group(1), match.group(2)
        if number is not None:
            operands.append(float(number))
            continue
        if word is None:
            operands.clear()
            continue
        op = word.decode("latin-1")
        if op == "q":
            stack.append(ctm)
        elif op == "Q":
            if stack:
                ctm = stack.pop()
        elif op == "cm" and len(operands) >= 6:
            ctm = _mul(tuple(operands[-6:]), ctm)
        elif op == "m" and len(operands) >= 2:
            if cur:
                subs.append(cur)
            cur = [_at(ctm, operands[-2], operands[-1])]
        elif op == "l" and len(operands) >= 2:
            cur.append(_at(ctm, operands[-2], operands[-1]))
        elif op == "c" and len(operands) >= 6:
            for i in (0, 2, 4):
                cur.append(_at(ctm, operands[-6+i], operands[-6+i+1]))
        elif op in ("v", "y") and len(operands) >= 4:
            for i in (0, 2):
                cur.append(_at(ctm, operands[-4+i], operands[-4+i+1]))
        elif op == "re" and len(operands) >= 4:
            x, y, w, h = operands[-4:]
            if cur:
                subs.append(cur)
            subs.append([_at(ctm, x, y), _at(ctm, x+w, y),
                         _at(ctm, x+w, y+h), _at(ctm, x, y+h)])
            cur = []
        elif op == "h":
            if cur:
                subs.append(cur); cur = []
        elif op in ("f", "f*", "F", "b", "b*", "B", "B*"):
            if cur:
                subs.append(cur)
            if subs:
                out.append(subs)
            cur, subs = [], []
        elif op in ("n", "S", "s"):
            cur, subs = [], []
        operands.clear()
    return out


def _glyphs(page) -> list[dict]:
    found = []
    for shape in _shapes(page.get_contents().get_data()):
        pts = [p for sub in shape for p in sub]
        if not pts:
            continue
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if not (1 <= w <= 20 and 3 <= h <= 20):
            continue          # page furniture, not a letter
        found.append({"subs": shape, "x0": min(xs), "y0": min(ys),
                      "x1": max(xs), "y1": max(ys), "w": w, "h": h})
    return found


def _lines(glyphs: list[dict], tol: float = 2.5) -> list[list[dict]]:
    glyphs = sorted(glyphs, key=lambda g: (round(g["y0"]), g["x0"]))
    out, cur = [], [glyphs[0]]
    for a, b in zip(glyphs, glyphs[1:]):
        if abs(b["y0"] - cur[-1]["y0"]) <= tol:
            cur.append(b)
        else:
            out.append(sorted(cur, key=lambda g: g["x0"])); cur = [b]
    out.append(sorted(cur, key=lambda g: g["x0"]))
    return out


def _words(line: list[dict]) -> list[list[dict]]:
    out, cur = [], [line[0]]
    for a, b in zip(line, line[1:]):
        if b["x0"] - a["x1"] > max(1.6, a["w"] * 0.55):
            out.append(cur); cur = [b]
        else:
            cur.append(b)
    out.append(cur)
    return out


def _cards(page) -> list[dict]:
    """Split a page into cards.

    Cards interleave across the page, so looking for gaps finds nothing.
    Every card prints Username and Password in the same place though, and
    those give an exact anchor for each one.
    """
    glyphs = _glyphs(page)
    if not glyphs:
        return []
    marks = []
    for line in _lines(glyphs):
        words = _words(line)
        if len(words) >= 2 and len(words) % 2 == 0 and all(len(w) == 8 for w in words):
            for i in range(0, len(words), 2):
                pair = words[i] + words[i + 1]
                marks.append({"x0": pair[0]["x0"], "x1": pair[-1]["x1"],
                              "y": line[0]["y0"]})
    if not marks:
        return []
    width = max(m["x1"] - m["x0"] for m in marks)
    rows = sorted({round(m["y"]) for m in marks})
    pitch = min((b - a for a, b in zip(rows, rows[1:])), default=80) or 80

    out = []
    for m in marks:
        cx = (m["x0"] + m["x1"]) / 2
        out.append({"x": cx, "y": m["y"], "glyphs": [
            g for g in glyphs
            if abs((g["x0"] + g["x1"]) / 2 - cx) <= width * 0.62
            and -pitch * 0.62 <= g["y0"] - m["y"] <= pitch * 0.62]})
    # Top of the page first. The sheet's own transform makes y grow upward,
    # so sorting ascending returns the page upside down and every card number
    # lands against the wrong card.
    out.sort(key=lambda c: (-round(c["y"] / pitch), c["x"]))
    return out


def _card_lines(card: dict) -> list[list[list[dict]]]:
    return [_words(ln) for ln in _lines(card["glyphs"])]


def _title(card):
    for ln in _card_lines(card):
        if len(ln) >= 3 and len(ln[0]) == 8 and len(ln[1]) == 4:
            return ln
    return None


def _number_glyphs(card) -> list[dict]:
    """The digits of the card's printed number, without its brackets."""
    ln = _title(card)
    if not ln:
        return []
    tail = [g for w in ln[2:] for g in w]
    return tail[1:-1] if len(tail) >= 3 else []


def _value_words(card):
    """The username and password actually printed on the card."""
    for ln in _card_lines(card):
        if len(ln) == 2 and len(ln[0]) >= 2 and len(ln[1]) >= 2:
            if _title(card) and ln[0] is not _title(card)[0]:
                return ln[0], ln[1]
    return None, None


def _signature(g: dict) -> frozenset:
    """A shape's fingerprint: its outline drawn on a small grid, so the same
    letter printed anywhere on the page gives the same pattern."""
    x0, y0 = g["x0"], g["y0"]
    w, h = max(g["w"], .01), max(g["h"], .01)
    cells = set()
    for sub in g["subs"]:
        for k in range(len(sub)):
            ax, ay = sub[k]
            bx, by = sub[(k + 1) % len(sub)]
            for step in range(25):
                t = step / 24
                px, py = ax + (bx - ax) * t, ay + (by - ay) * t
                cells.add((min(GRID_W - 1, int((px - x0) / w * GRID_W)),
                           min(GRID_H - 1, int((py - y0) / h * GRID_H))))
    return frozenset(cells)


def _similar(a: frozenset, b: frozenset) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


# --------------------------------------------------------------------------
# The learned alphabet
# --------------------------------------------------------------------------

def load_alphabet() -> list[dict]:
    """Shapes named on earlier sheets, so nothing is learned twice."""
    from backend.db import setting
    try:
        saved = json.loads(setting(ALPHABET_KEY, "[]"))
    except ValueError:
        return []
    out = []
    for item in saved:
        try:
            cells = frozenset(tuple(c) for c in item["cells"])
        except Exception:
            continue
        out.append({"cells": cells, "w": item["w"], "h": item["h"],
                    "label": item["label"]})
    return out


def save_alphabet(alphabet: list[dict]) -> None:
    from backend.db import set_setting
    set_setting(ALPHABET_KEY, json.dumps(
        [{"cells": sorted(list(c) for c in a["cells"]),
          "w": round(a["w"], 1), "h": round(a["h"], 1), "label": a["label"]}
         for a in alphabet if a["label"]]))


def forget_alphabet() -> None:
    from backend.db import set_setting
    set_setting(ALPHABET_KEY, "[]")


def _name_from(alphabet: list[dict], group: dict) -> str | None:
    best, score = None, 0.0
    for known in alphabet:
        if abs(known["w"] - group["w"]) > 0.7 or abs(known["h"] - group["h"]) > 0.7:
            continue
        found = _similar(group["cells"], known["cells"])
        if found >= MATCH and found > score:
            best, score = known["label"], found
    return best


def outline_svg(group: dict, size: int = 34) -> str:
    """Draw a shape so somebody can see which letter they are naming.

    Built from the outline itself rather than a picture of the page, so it
    stays sharp and needs nothing installed to render.
    """
    g = group["sample"]
    x0, y0 = g["x0"], g["y0"]
    w, h = max(g["w"], .01), max(g["h"], .01)
    scale = (size - 6) / max(w, h)
    paths = []
    for sub in g["subs"]:
        if len(sub) < 3:
            continue
        points = " ".join(
            "%.1f,%.1f" % (3 + (px - x0) * scale, size - 3 - (py - y0) * scale)
            for px, py in sub)
        paths.append('<polygon points="%s"/>' % points)
    return ('<svg viewBox="0 0 %d %d" width="%d" height="%d" '
            'xmlns="http://www.w3.org/2000/svg" fill="currentColor" '
            'fill-rule="evenodd">%s</svg>'
            % (size, size, size, size, "".join(paths)))


# --------------------------------------------------------------------------
# Reading a whole file
# --------------------------------------------------------------------------

def read_sheet(path: str) -> dict:
    """Read every card on a printed sheet.

    Returns the cards in printed order with whatever could be made out, plus
    the shapes still needing a name. Nothing is written anywhere.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    cards = []
    for page in reader.pages:
        cards.extend(_cards(page))
    if not cards:
        return {"readable": False, "cards": [], "unknown": [], "pages": len(reader.pages),
                "reason": "No voucher cards could be made out on that file."}

    # Group every shape on the sheet.
    groups: list[dict] = []
    for card in cards:
        for g in card["glyphs"]:
            signature = _signature(g)
            hit = None
            for grp in groups:
                if abs(grp["w"] - g["w"]) > 0.7 or abs(grp["h"] - g["h"]) > 0.7:
                    continue
                score = _similar(signature, grp["cells"])
                if score >= MATCH and (hit is None or score > hit[1]):
                    hit = (grp, score)
            if hit:
                hit[0]["count"] += 1
                g["group"] = hit[0]
            else:
                grp = {"cells": signature, "w": g["w"], "h": g["h"],
                       "count": 1, "label": None, "sample": g}
                groups.append(grp)
                g["group"] = grp

    # What we already know from earlier sheets.
    alphabet = load_alphabet()
    for grp in groups:
        grp["label"] = _name_from(alphabet, grp)

    # The sheet names its own digits: card 1 prints [1], card 42 prints [42].
    for number, card in enumerate(cards, start=1):
        for g, ch in zip(_number_glyphs(card), str(number)):
            if g["group"]["label"] is None:
                g["group"]["label"] = ch

    # And it spells these words on every card, in the same weight as the
    # values, which names most of the capitals.
    for card in cards:
        title = _title(card)
        if not title:
            continue
        for word, text in ((title[0], "PLATINUM"), (title[1], "SPOT")):
            if len(word) == len(text):
                for g, ch in zip(word, text):
                    if g["group"]["label"] is None:
                        g["group"]["label"] = ch

    def spell(word):
        return "".join(g["group"]["label"] or "\u00b7" for g in (word or []))

    out = []
    for number, card in enumerate(cards, start=1):
        user, password = _value_words(card)
        out.append({"number": number,
                    "username": spell(user),
                    "password": spell(password),
                    "complete": user is not None and password is not None
                                and "\u00b7" not in spell(user) + spell(password)})

    unknown = [g for g in groups
               if not g["label"] and any(
                   g is x["group"] for card in cards
                   for word in _value_words(card) if word for x in word)]
    unknown.sort(key=lambda g: -g["count"])

    return {"readable": True, "pages": len(reader.pages), "cards": out,
            "groups": groups, "unknown": unknown,
            "complete": sum(1 for c in out if c["complete"]),
            "reason": ""}


def learn(path: str, names: dict[int, str]) -> int:
    """Remember what somebody just named, for every sheet from now on."""
    found = read_sheet(path)
    if not found["readable"]:
        raise ValueError(found["reason"])
    alphabet = load_alphabet()
    added = 0
    for index, letter in names.items():
        letter = (letter or "").strip()
        if not letter or index >= len(found["unknown"]):
            continue
        group = found["unknown"][index]
        alphabet.append({"cells": group["cells"], "w": group["w"],
                         "h": group["h"], "label": letter[0]})
        added += 1
    save_alphabet(alphabet)
    return added


def fill_from_sheet(intake_id: int, path: str) -> dict:
    """Write what was read onto the cards already registered from this sheet."""
    found = read_sheet(path)
    if not found["readable"]:
        raise ValueError(found["reason"])
    stored = rows("SELECT id, code FROM vouchers WHERE intake_id = ? ORDER BY id",
                  (intake_id,))
    if not stored:
        raise ValueError("That sheet has no cards registered against it.")

    filled = partial = 0
    for card, voucher in zip(found["cards"], stored):
        if not card["username"] or "\u00b7" in card["username"]:
            partial += 1
            continue
        execute("UPDATE vouchers SET username = ?, secret = ? WHERE id = ?",
                (card["username"], card["password"], voucher["id"]))
        filled += 1
    return {"filled": filled, "unreadable": partial,
            "cards": len(found["cards"]), "registered": len(stored)}