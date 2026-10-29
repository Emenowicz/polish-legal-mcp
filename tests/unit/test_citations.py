import pytest

from polish_legal_mcp.errors import InvalidInputError
from polish_legal_mcp.models import ActId
from polish_legal_mcp.parsers.citations import alias_act_id, parse_citation, title_search_terms


def test_full_statutory_citation():
    # Synthetic citation string: exercises the parser only, no claim that the unit exists.
    parsed = parse_citation(
        "art. 11 ust. 1 pkt 2 lit. a ustawy z dnia 21 sierpnia 1997 r. o gospodarce "
        "nieruchomościami (Dz. U. z 2024 r. poz. 1145)"
    )
    assert (parsed.article, parsed.paragraph, parsed.paragraph_kind) == ("11", "1", "ust.")
    assert (parsed.point, parsed.letter) == ("2", "a")
    assert parsed.publication == "DU/2024/1145"
    assert parsed.act_text == "ustawy z dnia 21 sierpnia 1997 r. o gospodarce nieruchomościami"


def test_code_alias_with_section_sign():
    parsed = parse_citation("art. 61 § 1 k.p.a.")
    assert (parsed.article, parsed.paragraph, parsed.paragraph_kind) == ("61", "1", "§")
    assert parsed.known_alias == "k.p.a."
    assert alias_act_id(parsed) == ActId("DU", 1960, 168)


def test_regulation_paragraph_as_main_unit():
    parsed = parse_citation("§ 3 ust. 2 rozporządzenia (Dz.U. 2019 poz. 583)")
    assert parsed.article == "§ 3"
    assert (parsed.paragraph, parsed.paragraph_kind) == ("2", "ust.")
    assert parsed.publication == "DU/2019/583"


def test_pre_2012_publication_with_journal_number():
    parsed = parse_citation("art. 5 ustawy (Dz. U. z 1964 r. Nr 16, poz. 93)")
    assert parsed.publication == "DU/1964/93"


def test_eli_identifier_in_citation():
    assert parse_citation("art. 61 DU/1960/168").publication == "DU/1960/168"


def test_citation_without_unit_keeps_act_text():
    parsed = parse_citation("ustawa o gospodarce nieruchomościami")
    assert parsed.article is None
    assert parsed.act_text == "ustawa o gospodarce nieruchomościami"


@pytest.mark.parametrize("raw", ["", "   ", "x" * 1001])
def test_rejects_empty_or_oversized(raw):
    with pytest.raises(InvalidInputError):
        parse_citation(raw)


def test_title_search_terms():
    assert title_search_terms(
        "ustawy z dnia 21 sierpnia 1997 r. o gospodarce nieruchomościami"
    ) == (
        "Ustawa",
        "o gospodarce nieruchomościami",
        1997,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DU/1960/168", ActId("DU", 1960, 168)),
        ("Dz.U. 2000 poz. 856", ActId("DU", 2000, 856)),
        ("Dz. U. z 2025 r. poz. 1691", ActId("DU", 2025, 1691)),
        ("Dz.U. 1960 nr 30 poz. 168", ActId("DU", 1960, 168)),
        ("M.P. 2020 poz. 12", ActId("MP", 2020, 12)),
        ("WDU19600300168", ActId("DU", 1960, 168)),
    ],
)
def test_act_id_formats(raw, expected):
    assert ActId.parse(raw) == expected


@pytest.mark.parametrize(
    "raw", ["DZ/2015/1", "DU/1800/1", "https://evil.example/DU/2015/1", "../acts"]
)
def test_act_id_rejects_invalid(raw):
    with pytest.raises(InvalidInputError):
        ActId.parse(raw)
