"""Money, quantities, dates and phone numbers."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_DIGITS = re.compile(r"[^0-9\-]")


def parse_int(raw: object, default: int = 0) -> int:
    """Forgiving: '1,200', '1 200', 'UGX 1200/=' all mean 1200."""
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip()
    if not text:
        return default
    negative = text.startswith("-")
    digits = _DIGITS.sub("", text).lstrip("-")
    if not digits:
        return default
    value = int(digits)
    return -value if negative else value


def money(value: int | None) -> str:
    return f"{int(value or 0):,}"


def signed_money(value: int | None) -> str:
    amount = int(value or 0)
    return "0" if amount == 0 else f"{amount:+,}"


def qty(value: int | None) -> str:
    return f"{int(value or 0):,}"


def pct(basis_points: int | None) -> str:
    return f"{(int(basis_points or 0) / 100):g}%"


def today() -> str:
    return date.today().isoformat()


def parse_date(raw: object, default: str | None = None) -> str:
    text = str(raw).strip() if raw is not None else ""
    if text:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    return default or today()


def pretty_date(value: str | None) -> str:
    if not value:
        return "-"
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d %b %Y")
    except ValueError:
        return str(value)


def shift(day: str, days: int) -> str:
    return (date.fromisoformat(day[:10]) + timedelta(days=days)).isoformat()


def week_bounds(day: str) -> tuple[str, str]:
    """Monday to Sunday containing `day`."""
    d = date.fromisoformat(day[:10])
    start = d - timedelta(days=d.weekday())
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


def month_bounds(day: str) -> tuple[str, str]:
    d = date.fromisoformat(day[:10])
    first = d.replace(day=1)
    last = (first.replace(year=first.year + (first.month == 12),
                          month=(first.month % 12) + 1) - timedelta(days=1))
    return first.isoformat(), last.isoformat()


def year_bounds(day: str) -> tuple[str, str]:
    year = date.fromisoformat(day[:10]).year
    return f"{year}-01-01", f"{year}-12-31"


def clean_phone(raw: str, country: str = "256") -> str:
    """Turn a locally written number into the international form WhatsApp and
    Telegram links need. '0771 234 567' becomes '256771234567'."""
    digits = re.sub(r"[^0-9+]", "", str(raw or ""))
    digits = digits.lstrip("+")
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = country + digits[1:]
    elif not digits.startswith(country) and len(digits) <= 10:
        digits = country + digits
    return digits
