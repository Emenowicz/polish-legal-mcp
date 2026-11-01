"""Amending-act detection from ELI titles (real titles, api.sejm.gov.pl, checked 2026-11-01)."""

import pytest

from polish_legal_mcp.services.legal import summarize_act

BASE = [
    # Base acts whose title also amends another act: they must stay search candidates.
    "Ustawa z dnia 21 czerwca 2001 r. o ochronie praw lokatorów, mieszkaniowym zasobie gminy i o zmianie Kodeksu cywilnego.",
    "Ustawa z dnia 23 maja 2024 r. o bonie energetycznym oraz o zmianie niektórych ustaw w celu ograniczenia cen energii elektrycznej, gazu ziemnego i ciepła systemowego",
    "Ustawa z dnia 15 września 2000 r. o grupach producentów rolnych i ich związkach oraz o zmianie innych ustaw.",
    "Ustawa z dnia 26 czerwca 1974 r. Kodeks pracy.",
]
AMENDING = [
    "Ustawa z dnia 20 maja 2021 r. o zmianie ustawy - Kodeks cywilny oraz niektórych innych ustaw",
    "Ustawa z 16 kwietnia 2004 r. o zmianie ustawy o ochronie konkurencji i konsumentów oraz o zmianie niektórych innych ustaw",
    # Typos in the date part of ELI titles.
    "Ustawa z dnia 24 września 24 września 2010 r. o zmianie ustawy o podatku dochodowym od osób fizycznych",
    "Rozporządzenie Ministra Finansów dnia 7 kwietnia 2016 r. zmieniające rozporządzenie w sprawie szczególnych zasad rachunkowości domów maklerskich",
    # Older style.
    "Rozporządzenie Ministra Zdrowia z dnia 26 marca 2024 r. w sprawie zmiany rozporządzenia zmieniającego rozporządzenie w sprawie programu pilotażowego",
]


@pytest.mark.parametrize("title", BASE)
def test_base_acts_are_not_amending(title):
    assert not summarize_act({"ELI": "DU/2001/733", "title": title}).is_amending_act


@pytest.mark.parametrize("title", AMENDING)
def test_amending_acts_are_detected(title):
    assert summarize_act({"ELI": "DU/2021/1509", "title": title}).is_amending_act
