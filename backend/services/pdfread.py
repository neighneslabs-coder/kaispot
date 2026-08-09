"""Reading voucher codes out of an uploaded PDF sheet.

Voucher sheets come off Mikhmon and other generators in many layouts, so
nothing here assumes a particular one. The text is pulled out of the file, a
pattern is run over it, and the result is always shown for approval before a
single voucher is taken into stock.

Two things stop the common failure. First, when a package has a code prefix
set, the pattern is built from it, so a sheet printing both a username and a
password does not have its count doubled by the passwords. Second, a pattern
with two bracketed groups captures a code and its password together, which is
how most generated sheets are laid out.
"""

from __future__ import annotations

import re

# Any run of 4 to 16 letters, digits and dashes containing at least one digit.
# That is what separates a code from the words printed around it on the page.
DEFAULT_PATTERN = r"\b(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{3,15}\b"

NOISE = {
    "wifi", "wi-fi", "http", "https", "www", "com", "net", "org", "ugx",
    "password", "username", "user", "voucher", "code", "hotspot", "login",
    "valid", "validity", "expiry", "price", "kaispot", "mikhmon", "profile",
    "page", "time", "limit", "data", "unlimited", "shs",
}


def suggest_pattern(prefix: str = "") -> str:
    """A pattern for one package. With a prefix set this is far more precise
    than the generic one, and precision here is what keeps the count honest."""
    prefix = (prefix or "").strip()
    if not prefix:
        return DEFAULT_PATTERN
    return rf"\b({re.escape(prefix)}[A-Za-z0-9-]{{1,14}})\b"


def extract_text(path: str) -> tuple[str, int]:
    """All the text in the file, and how many pages it had."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            pages = len(pdf.pages)
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        if text.strip():
            return text, pages
    except Exception:
        pass
    from pypdf import PdfReader
    reader = PdfReader(path)
    return ("\n".join((page.extract_text() or "") for page in reader.pages),
            len(reader.pages))


def find_entries(text: str, pattern: str = DEFAULT_PATTERN) -> list[dict]:
    """Every candidate voucher, in the order it appears on the page.

    Order matters: sheets are printed in sequence and the office expects the
    first voucher issued to be the first one on the sheet.

    A pattern with two bracketed groups is read as code then password.
    """
    try:
        finder = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"That search pattern is not valid: {exc}") from exc

    paired = finder.groups >= 2
    seen: set[str] = set()
    entries: list[dict] = []
    for match in finder.finditer(text):
        if paired:
            code, secret = match.group(1), match.group(2)
        elif finder.groups:
            code, secret = match.group(1), ""
        else:
            code, secret = match.group(0), ""
        code = (code or "").strip()
        if not code or code.lower() in NOISE or code.lower() in seen:
            continue
        seen.add(code.lower())
        entries.append({"code": code, "secret": (secret or "").strip()})
    return entries


def find_codes(text: str, pattern: str = DEFAULT_PATTERN) -> list[str]:
    return [e["code"] for e in find_entries(text, pattern)]


def inspect(path: str, pattern: str = DEFAULT_PATTERN) -> dict:
    """What we would take in, without taking anything in."""
    text, pages = extract_text(path)
    if not text.strip():
        return {"pages": pages, "entries": [], "codes": [], "count": 0,
                "text_found": False, "sample": ""}
    entries = find_entries(text, pattern)
    return {
        "pages": pages,
        "entries": entries,
        "codes": [e["code"] for e in entries],
        "count": len(entries),
        "text_found": True,
        "with_password": sum(1 for e in entries if e["secret"]),
        "sample": "\n".join(text.splitlines()[:12]),
    }
