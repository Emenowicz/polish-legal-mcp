"""EU act identifiers and article extraction on real Publications Office documents (2026-10-29)."""

import gzip

import pytest

from polish_legal_mcp.parsers.eu_html import extract_eu_unit
from polish_legal_mcp.parsers.legal_html import UnitLookupError
from polish_legal_mcp.services.eu_law import resolve_eu_act
from tests.conftest import FIXTURES


@pytest.mark.parametrize(
    ("text", "celex"),
    [
        ("32016R0679", "32016R0679"),
        ("RODO", "32016R0679"),
        ("art. 28 RODO", "32016R0679"),
        ("rozporządzenie Parlamentu Europejskiego i Rady (UE) 2016/679", "32016R0679"),
        ("rozporządzenia (WE) nr 1049/2001", "32001R1049"),
        ("rozporządzenie (UE, Euratom) 2018/1046", "32018R1046"),
        ("dyrektywa 2014/24/UE", "32014L0024"),
        ("dyrektywy Rady 93/13/EWG", "31993L0013"),
        ("decyzja (UE) 2019/1", "32019D0001"),
    ],
)
def test_resolve_eu_act(text, celex):
    assert resolve_eu_act(text) == celex


@pytest.mark.parametrize(
    "text", ["ustawa o gospodarce nieruchomościami", "k.p.a.", "Dz.U. 2024 poz. 1"]
)
def test_polish_acts_are_not_eu(text):
    assert resolve_eu_act(text) is None


def _doc(name: str) -> str:
    return gzip.decompress((FIXTURES / "eurlex" / name).read_bytes()).decode("utf-8")


@pytest.fixture(scope="module")
def oj() -> str:
    return _doc("32016R0679.xhtml.gz")


@pytest.fixture(scope="module")
def consolidated() -> str:
    return _doc("02016R0679-20160504.xhtml.gz")


def test_consolidated_version_carries_polish_corrigenda(oj, consolidated):
    original, _ = extract_eu_unit(oj, "4", point="1")
    corrected, info = extract_eu_unit(consolidated, "4", point="1")
    assert original.text.startswith("1) „dane osobowe” oznaczają informacje o")
    assert corrected.text.startswith("1) „dane osobowe” oznaczają wszelkie informacje o")
    assert info.consolidated
    assert info.incorporated_celex >= {"32016R0679R(02)", "32016R0679R(03)"}


def test_paragraph_and_letter_in_both_formats(oj, consolidated):
    for html in (oj, consolidated):
        unit, info = extract_eu_unit(html, "28", "3", letter="a")
        assert unit.path == ["Artykuł 28 – Podmiot przetwarzający", "ust. 3", "lit. a"]
        assert unit.text.startswith(
            "a) przetwarza dane osobowe wyłącznie na udokumentowane polecenie"
        )
        assert info.oj and info.oj.format() == "Dz. Urz. UE L 119 z 4.5.2016, str. 1"
    fixed, _ = extract_eu_unit(consolidated, "28", "9")
    assert (
        fixed.text
        == "9. Umowa lub inny akt prawny, o których mowa w ust. 3 i 4, mają formę pisemną, w tym formę elektroniczną."
    )


def test_change_markers_removed(consolidated):
    unit, _ = extract_eu_unit(consolidated, "4")
    assert "►" not in unit.text and "▼" not in unit.text and "◄" not in unit.text


@pytest.mark.parametrize(
    ("args", "fragment"),
    [
        (("999",), "Nie znaleziono art. 999"),
        (("28", "99"), "nie zawiera ust. 99"),
        (("28", "3", None, "z"), "nie zawiera lit. z"),
    ],
)
def test_missing_units_never_guessed(consolidated, args, fragment):
    with pytest.raises(UnitLookupError) as err:
        extract_eu_unit(consolidated, *args)
    assert err.value.reason == "not_found" and fragment in err.value.message


def test_old_format_without_article_ids_is_unsupported():
    with pytest.raises(UnitLookupError) as err:
        extract_eu_unit(_doc("32001R1049.html.gz"), "2")
    assert err.value.reason == "ambiguous"
