"""Act index pieces. ELI inputs are real fixtures (struct of DU/2015/1322 and HTML of the
consolidated text DU/2024/1835, captured 2026-10-29; search page for górnictwo siarki); the
titles in the BM25 test are synthetic."""

import gzip
from datetime import date

from polish_legal_mcp.services.act_index import (
    ActIndex,
    article_label,
    articles,
    headings,
    is_generic,
    subject,
    text_key,
)
from polish_legal_mcp.services.legal import interleave, lexical_first
from tests.conftest import FIXTURES, load_json


def test_subject_drops_the_date():
    title = (
        "Ustawa z dnia 21 czerwca 2001 r. o ochronie praw lokatorów, mieszkaniowym zasobie gminy."
    )
    assert subject(title) == "Ustawa o ochronie praw lokatorów, mieszkaniowym zasobie gminy"


def test_headings_of_a_real_struct_skip_only_generic_ones():
    found = headings(load_json("eli/DU_2015_1322.struct.json"))
    assert "Agencja Mienia Wojskowego" in found
    # Starts with "Przepisy" but names a subject: must stay.
    assert "Przepisy szczególne dotyczące lokali mieszkalnych" in found
    assert not any(is_generic(h) for h in found)
    assert is_generic("Przepisy przejściowe i końcowe")
    assert is_generic("Zmiany w przepisach obowiązujących")


def test_articles_of_a_consolidated_text_skip_articles_quoted_by_the_notice():
    html = gzip.decompress((FIXTURES / "eli/DU_2024_1835.html.gz").read_bytes()).decode()
    units = articles(html)
    # The notice's own text quotes "Art. 84" of an amending act (id pass_2-arti_84): not indexed.
    assert [article_label(u) for u, _ in units] == [f"art. {n}" for n in range(1, 10)]
    assert "Siarkopol" in units[0][1]
    assert article_label("chpt_2-arti_15a") == "art. 15a"
    assert article_label("arti_180_1") == "art. 180¹"


def test_text_key_follows_the_newest_consolidated_text():
    item = next(
        i
        for i in load_json("eli/search_gornictwo_siarki.json")["items"]
        if i["ELI"] == "DU/2000/856"
    )
    assert text_key(item) == "DU/2024/1835|True"


def test_titles_return_one_hit_per_act_with_the_matching_heading(tmp_path):
    # Synthetic: the Labour Code's title says nothing about parenthood, its heading does.
    acts = [
        {"ELI": "DU/1974/141", "title": "Ustawa z dnia 26 czerwca 1974 r. Kodeks pracy."},
        {"ELI": "DU/1984/268", "title": "Ustawa z dnia 15 listopada 1984 r. o podatku rolnym."},
    ]
    heads = [["Uprawnienia pracowników związane z rodzicielstwem"], []]
    index = ActIndex(tmp_path / "unused.sqlite", acts, heads, date(2026, 11, 1))
    hits = index.titles("uprawnienia związane z rodzicielstwem")
    assert [(h.act["ELI"], h.label) for h in hits] == [
        ("DU/1974/141", "nagłówek „Uprawnienia pracowników związane z rodzicielstwem”")
    ]


def test_ranking_helpers():
    assert interleave(["a", "b", "c"], ["x", "b"]) == ["a", "x", "b", "c"]
    assert lexical_first(["a", "b", "c", "d"], ["x", "a"], keep=3) == ["a", "b", "c", "x", "d"]
