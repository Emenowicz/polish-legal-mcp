"""Detect staged entry into force ("z wyjątkiem art. X, który wchodzi w życie ...") in act text.

Only a conservative signal: if any exception date cannot be computed or is later than the
requested date, temporal applicability must stay UNVERIFIED.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "września": 9, "października": 10, "listopada": 11, "grudnia": 12,
}  # fmt: skip
NUMBER_WORDS = {
    "jednego": 1, "dwóch": 2, "trzech": 3, "czterech": 4, "pięciu": 5, "sześciu": 6,
    "siedmiu": 7, "ośmiu": 8, "dziewięciu": 9, "dziesięciu": 10, "dwunastu": 12,
    "czternastu": 14, "osiemnastu": 18, "dwudziestu": 20, "trzydziestu": 30,
}  # fmt: skip

DATE_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", re.IGNORECASE)
CLAUSE_RE = re.compile(
    r"(?:Ustawa|Rozporządzenie|Obwieszczenie|Uchwała|Zarządzenie|Kodeks)\s+wchodzi\s+w\s+życie",
    re.IGNORECASE,
)
RELATIVE_RE = re.compile(
    r"po\s+upływie\s+(\d+|\w+)\s+(dni|dnia|miesięcy|miesiąca|miesiące|lat|roku|lata)\s+od\s+dnia\s+"
    r"(?:jej\s+|jego\s+)?ogłoszenia",
    re.IGNORECASE,
)
DAY_AFTER_RE = re.compile(
    r"z\s+dniem\s+następującym\s+po\s+dniu\s+(?:jej\s+|jego\s+)?ogłoszenia", re.I
)
ON_PROMULGATION_RE = re.compile(r"z\s+dniem\s+(?:jej\s+|jego\s+)?ogłoszenia", re.IGNORECASE)
ANY_TRIGGER_RE = re.compile(r"wchodz[iąa]\s+w\s+życie|wejdzie\s+w\s+życie", re.IGNORECASE)
CLAUSE_WINDOW = 1500


@dataclass
class StagedEntry:
    clause: str
    unresolved: bool  # some exception date could not be computed
    latest_date: date | None


def parse_dates(text: str) -> list[date]:
    found: list[date] = []
    for day, month, year in DATE_RE.findall(text):
        try:
            found.append(date(int(year), MONTHS[month.lower()], int(day)))
        except ValueError:
            continue
    return found


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _relative_dates(clause: str, promulgation: date | None) -> tuple[list[date], bool]:
    """Compute dates for relative periods; second value is True if something was not computable."""
    dates: list[date] = []
    unresolved = False
    for amount, unit in RELATIVE_RE.findall(clause):
        number = int(amount) if amount.isdigit() else NUMBER_WORDS.get(amount.lower())
        if number is None or promulgation is None:
            unresolved = True
            continue
        if unit.startswith("d"):
            end = promulgation + timedelta(days=number)
        elif unit.startswith("mies"):
            end = _add_months(promulgation, number)
        else:
            end = _add_months(promulgation, 12 * number)
        dates.append(end + timedelta(days=1))
    if DAY_AFTER_RE.search(clause):
        if promulgation is None:
            unresolved = True
        else:
            dates.append(promulgation + timedelta(days=1))
    if ON_PROMULGATION_RE.search(clause):
        if promulgation is None:
            unresolved = True
        else:
            dates.append(promulgation)
    return dates, unresolved


def find_staged_entry(act_text: str, promulgation: date | None) -> StagedEntry | None:
    """Return the act's own entry-into-force clause if it contains exceptions ('z wyjątkiem')."""
    for match in CLAUSE_RE.finditer(act_text):
        clause = act_text[match.start() : match.start() + CLAUSE_WINDOW]
        next_article = re.search(r"\sArt\.\s*\d+[a-z]*\.\s", clause[20:])
        if next_article:
            clause = clause[: 20 + next_article.start()]
        if "z wyjątkiem" not in clause.lower():
            continue
        absolute = parse_dates(clause)
        relative, unresolved = _relative_dates(clause, promulgation)
        triggers = len(ANY_TRIGGER_RE.findall(clause))
        # Every trigger phrase needs at least one computable date; otherwise stay conservative.
        if len(absolute) + len(relative) < triggers:
            unresolved = True
        dates = absolute + relative
        return StagedEntry(
            clause=clause.strip(), unresolved=unresolved, latest_date=max(dates) if dates else None
        )
    return None
