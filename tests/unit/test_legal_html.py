"""Struct/HTML extraction on real ELI documents (act DU/2015/1322, consolidated text DU/2024/98)."""

import gzip

import pytest

from polish_legal_mcp.parsers.legal_html import (
    UnitLookupError,
    act_body_text,
    extract_unit,
    find_unit,
    iter_toc,
    parse_html,
)
from tests.conftest import FIXTURES, load_json


@pytest.fixture(scope="module")
def tj():
    struct = load_json("eli/DU_2024_98.struct.json")
    html = gzip.decompress((FIXTURES / "eli/DU_2024_98.html.gz").read_bytes()).decode()
    return struct, parse_html(html)


def test_consolidated_text_resolves_annex_not_notice_body(tj):
    struct, _ = tj
    # The notice body also contains an "art. 11" (of another act); the annex one must win.
    match = find_unit(struct, "11")
    assert match.node_id == "chpt_2-arti_11"
    assert match.path[0].startswith("Załącznik - Tekst jednolity")


def test_nested_unit_wording(tj):
    struct, doc = tj
    match = find_unit(struct, "11", "3", "2")
    unit = extract_unit(doc, match.node_id)
    assert unit is not None
    assert unit.text == "2) złożenia rezygnacji;"
    assert match.paragraph_kind == "ust."


def test_footnotes_separated_and_hidden_references_kept(tj):
    struct, doc = tj
    unit = extract_unit(doc, find_unit(struct, "4a").node_id)
    assert unit is not None
    assert (
        "W brzmieniu ustalonym przez art. 1 pkt 1 ustawy z dnia 17 sierpnia 2023"
        in unit.footnotes[0]
    )
    assert "W brzmieniu" not in unit.text
    assert unit.text.startswith("Art. 4a. Ilekroć w ustawie")


def test_repealed_marker_detected(tj):
    struct, doc = tj
    unit = extract_unit(doc, find_unit(struct, "7", "1", "7").node_id)
    assert unit is not None and unit.marker == "(uchylony)"


@pytest.mark.parametrize(
    ("args", "fragment"),
    [
        (("999",), "Nie znaleziono art. 999"),
        (("11", "99"), "nie zawiera jednostki ust./§ 99"),
        (("11", None, "1"), "nie zawiera jednostki pkt 1"),
    ],
)
def test_missing_units_never_guessed(tj, args, fragment):
    struct, _ = tj
    with pytest.raises(UnitLookupError) as err:
        find_unit(struct, *args)
    assert err.value.reason == "not_found"
    assert fragment in err.value.message


def test_explicit_section_sign_does_not_match_ustep(tj):
    struct, _ = tj
    with pytest.raises(UnitLookupError):
        find_unit(struct, "11", "§ 1")


def test_unknown_html_id_returns_none(tj):
    _, doc = tj
    assert extract_unit(doc, "chpt_99-arti_1") is None


def test_toc_skips_notice_body(tj):
    struct, _ = tj
    labels = [label for _, _, label, _ in iter_toc(struct)]
    assert not any(label.startswith("Treść obwieszczenia") for label in labels)
    assert "Art. 4a." in labels


def test_act_body_text_excludes_notice(tj):
    struct, doc = tj
    body = act_body_text(doc, struct)
    assert "Art. 138. Ustawa wchodzi w życie z dniem 1 października 2015 r." in body
    assert "Art. 84. Ustawa wchodzi w życie" not in body


def test_original_text_skips_quoted_articles_of_other_acts():
    struct = load_json("eli/DU_2015_1322.struct.json")
    match = find_unit(struct, "2")
    assert match.node_id == "chpt_1-arti_2"
