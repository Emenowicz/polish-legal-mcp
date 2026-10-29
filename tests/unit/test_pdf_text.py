"""PDF reading on the real consolidated text DU/2025/817 (AMW act, captured 2026-10-29)."""

from pathlib import Path

import pytest

from polish_legal_mcp.parsers.legal_html import (
    UnitLookupError,
    display_name,
    extract_unit,
    normalize_name,
    parse_html,
)
from polish_legal_mcp.parsers.pdf_text import (
    _line_text,
    find_pdf_unit,
    read_pdf,
    repeal_marker,
    same_wording,
)
from tests.conftest import FIXTURES


@pytest.fixture(scope="module")
def pdf():
    return read_pdf(Path(FIXTURES / "eli" / "DU_2025_817.pdf").read_bytes())


def test_page_headers_and_footnotes_removed(pdf):
    body = "\n".join(line.text for line in pdf.lines)
    assert "Dziennik Ustaw – 2 – Poz. 817" not in body
    assert not any(line.text.startswith("1) W brzmieniu ustalonym") for line in pdf.lines)
    assert any("W brzmieniu ustalonym" in f.text for f in pdf.footnotes)


def test_units_located_in_numbering_order(pdf):
    unit = find_pdf_unit(pdf, "11", "3", "2")
    assert unit.text == "2) złożenia rezygnacji;" and unit.page == 6
    assert unit.path == ["Art. 11.", "3.", "2)"]
    letter = find_pdf_unit(pdf, "1", point="2", letter="a")
    assert letter.text.startswith("a) będącym do dnia jego przekazania Agencji")


def test_repealed_marker(pdf):
    unit = find_pdf_unit(pdf, "7", "1", "7")
    assert repeal_marker(unit.text) == "(uchylony)"


@pytest.mark.parametrize(
    ("args", "fragment"), [(("999",), "Nie znaleziono art. 999"), (("11", "99"), "ust./§ 99")]
)
def test_missing_units_never_guessed(pdf, args, fragment):
    with pytest.raises(UnitLookupError) as err:
        find_pdf_unit(pdf, *args)
    assert fragment in err.value.message


def test_same_wording_ignores_pdf_spacing_and_hyphenation():
    html = "Ilekroć w ustawie jest mowa o towarzystwie budownictwa społecznego."
    pdf = "Ilekroć w ustawie jest mowa o to wa-\nrzystwie budownictwa społecznego."
    assert same_wording(html, pdf) == (True, None)
    assert same_wording(html, html.replace("społecznego", "socjalnego")) == (False, None)


def test_same_wording_updates_well_formed_references_only():
    html = "przepisów ustawy – Kodeks pracy (Dz. U. z 2023 r. poz. 1465)."
    pdf = "przepisów ustawy – Kodeks pracy (Dz. U. z 2025 r. poz. 277)."
    assert same_wording(html, pdf) == (
        True,
        "przepisów ustawy – Kodeks pracy (Dz. U. z 2025 r. poz. 277).",
    )
    garbled = "przepisów ustawy – Kodeks pracy (Dz. U. z 2025 r. po z. 277)."
    assert same_wording(html, garbled) == (False, None)


def test_superscripts_kept_and_footnote_markers_dropped():
    # Synthetic fragments (size, text) as pypdf reports them.
    assert (
        _line_text([(10, "Art. 22"), (6.5, "[1]"), (10, ". Za konsumenta")])
        == "Art. 22¹. Za konsumenta"
    )
    assert _line_text([(10, "Narodowej"), (6.5, "1"), (6.0, ")"), (10, ".")]) == "Narodowej."


@pytest.mark.parametrize(
    ("value", "canonical", "shown"),
    [
        ("22¹", "22_1", "22¹"),
        ("22^1", "22_1", "22¹"),
        ("22(1)", "22_1", "22¹"),
        ("art. 4a", "4a", "4a"),
        ("§ 2", "2", "2"),
        ("a)", "a", "a"),
    ],
)
def test_unit_number_forms(value, canonical, shown):
    assert normalize_name(value) == canonical
    assert display_name(value) == shown


def test_html_superscript_numbers_not_glued():
    doc = parse_html(
        '<html><body><div class="unit unit_arti" id="arti_22_1"><h3><b>Art.&nbsp;22<sup>1</sup>.</b></h3>'
        '<div class="unit-inner"><div data-template="xText">Por. art. 22<sup>1</sup> i art. 221.</div></div>'
        "</div></body></html>"
    )
    unit = extract_unit(doc, "arti_22_1")
    assert unit is not None
    assert unit.text == "Art. 22¹. Por. art. 22¹ i art. 221."
