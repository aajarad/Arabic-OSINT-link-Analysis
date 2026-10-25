"""Arabic date extraction for the timeline stage.

Recognizes Gregorian dates written with Arabic month names (Levantine/Iraqi
names, transliterated "yanayir" names and Maghrebi variants), bare years, and
numeric dd/mm/yyyy or yyyy-mm-dd forms. Handles Arabic-Indic digits. Returns
the raw surface span and an ISO-8601 (possibly partial) normalization.
Impossible calendar dates (e.g. 31/02/2023) are rejected.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..utils.arabic import fold_chars

# Month name -> month number. Keys are folded with ``fold_chars`` (alef
# variants, ta marbuta, ya) so spelling variants collapse to one entry.
_RAW_MONTHS = {
    # Levantine / Iraqi solar names
    "كانون الثاني": 1, "كانون ثاني": 1, "شباط": 2, "آذار": 3, "نيسان": 4,
    "أيار": 5, "حزيران": 6, "تموز": 7, "آب": 8, "أيلول": 9,
    "تشرين الأول": 10, "تشرين أول": 10, "تشرين الثاني": 11, "تشرين ثاني": 11,
    "كانون الأول": 12, "كانون أول": 12,
    # Transliterated Gregorian names (Mashriq)
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "إبريل": 4, "مايو": 5,
    "يونيو": 6, "يوليو": 7, "أغسطس": 8, "سبتمبر": 9, "أكتوبر": 10,
    "نوفمبر": 11, "ديسمبر": 12,
    # Maghrebi variants
    "ماي": 5, "يونيه": 6, "يوليوز": 7, "يوليه": 7, "غشت": 8, "شتنبر": 9,
    "نونبر": 11, "دجنبر": 12,
}
_MONTHS: Dict[str, int] = {}
for _k, _v in _RAW_MONTHS.items():
    _MONTHS[re.sub(r"\s+", " ", fold_chars(_k)).strip()] = _v

_MONTH_ALT = "|".join(
    re.escape(m).replace(r"\ ", r"\s+")
    for m in sorted(_MONTHS, key=len, reverse=True)
)
_L = "\u0621-\u064a"  # Arabic letters
_MONTH = rf"(?<![{_L}])(?P<month>{_MONTH_ALT})(?![{_L}])"

# "12 آذار 2011" / "12 آذار/مارس 2011" (day month year)
_DMY = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})\s*(?:من\s+|-\s*)?" + _MONTH +
    r"(?:\s*[/\-]\s*(?:" + _MONTH_ALT + r"))?\s*[,،]?\s*(?:عام\s+|سنة\s+)?(?P<year>\d{4})(?!\d)"
)
# "آذار 2011" (month year)
_MY = re.compile(
    _MONTH + r"(?:\s*[/\-]\s*(?:" + _MONTH_ALT + r"))?\s*[,،]?\s*(?:عام\s+|سنة\s+)?(?P<year>\d{4})(?!\d)"
)
# "12/03/2011" or "2011-03-12"
_NUMERIC = re.compile(
    r"(?<![\d/\-.])(?P<a>\d{1,4})[/\-.](?P<b>\d{1,2})[/\-.](?P<c>\d{1,4})(?![\d/\-]|\.\d)"
)
# bare year "عام 2011" / "سنة 2011" / "2011"
_YEAR = re.compile(r"(?<![\d/\-.])(?:عام\s+|سنة\s+)?(?P<year>(?:19|20)\d{2})(?![\d/\-]|\.\d)")


@dataclass
class DateMention:
    surface: str          # exact text as it appeared
    normalized: str       # ISO-8601 (possibly partial: YYYY, YYYY-MM, YYYY-MM-DD)
    start: int
    end: int
    precision: str        # 'day' | 'month' | 'year'


def _iso(year: int, month: Optional[int] = None, day: Optional[int] = None) -> str:
    if day and month:
        return f"{year:04d}-{month:02d}-{day:02d}"
    if month:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}"


def _valid(year: int, month: int, day: int) -> bool:
    if not (1000 <= year <= 2999 and 1 <= month <= 12):
        return False
    return 1 <= day <= calendar.monthrange(year, month)[1]


def extract_dates(text: str) -> List[DateMention]:
    """Extract date mentions from Arabic text. Overlapping matches are
    resolved by preferring the most precise (day > month > year)."""
    if not text:
        return []
    norm = fold_chars(text)  # length-preserving, so offsets map back to text
    found: List[DateMention] = []
    claimed: List[range] = []

    def overlaps(a: int, b: int) -> bool:
        return any(a < r.stop and r.start < b for r in claimed)

    def add(m: "re.Match[str]", iso: str, precision: str) -> None:
        s, e = m.start(), m.end()
        if overlaps(s, e):
            return
        found.append(DateMention(text[s:e], iso, s, e, precision))
        claimed.append(range(s, e))

    def month_no(name: str) -> int:
        return _MONTHS[re.sub(r"\s+", " ", name)]

    for m in _DMY.finditer(norm):
        year, month, day = int(m.group("year")), month_no(m.group("month")), int(m.group("day"))
        if _valid(year, month, day):
            add(m, _iso(year, month, day), "day")

    for m in _NUMERIC.finditer(norm):
        a, b, c = int(m.group("a")), int(m.group("b")), int(m.group("c"))
        if len(m.group("a")) == 4:      # YYYY-MM-DD
            year, month, day = a, b, c
        elif len(m.group("c")) == 4:    # DD-MM-YYYY
            year, month, day = c, b, a
        else:
            continue
        if _valid(year, month, day):
            add(m, _iso(year, month, day), "day")

    for m in _MY.finditer(norm):
        add(m, _iso(int(m.group("year")), month_no(m.group("month"))), "month")

    for m in _YEAR.finditer(norm):
        add(m, _iso(int(m.group("year"))), "year")

    found.sort(key=lambda d: d.start)
    return found
