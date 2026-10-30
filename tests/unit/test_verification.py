"""Pure verification rules (synthetic metadata, labelled as such)."""

from dataclasses import replace
from datetime import date
from typing import Any

from polish_legal_mcp.models import ActReference, CheckResult
from polish_legal_mcp.models import VerificationStatus as VS
from polish_legal_mcp.parsers.entry_into_force import StagedEntry
from polish_legal_mcp.services.legal import TemporalInputs, assess_act_in_force, assess_temporal

TODAY = date(2026, 10, 29)
OK = CheckResult(status=VS.VERIFIED, detail="ok")


BASE = TemporalInputs(
    as_of=TODAY,
    today=TODAY,
    act_check=OK,
    text_read=True,
    unit_found=True,
    source_label="Tekst jednolity DU/2024/1",
    state_date=date(2024, 1, 1),
    marker=None,
    changes=[],
    undated_changes=[],
    staged=None,
    footnote_dates=[],
)


def inputs(**overrides: Any) -> TemporalInputs:
    return replace(BASE, **overrides)


def ref(eli: str, d: date | None) -> ActReference:
    return ActReference(eli=eli, relation="Akty zmieniające", effective_date=d, api_url="x")


# --- act-level (synthetic metadata) ---------------------------------------------------------


def test_act_not_yet_in_force():
    meta = {"entryIntoForce": "2027-09-27", "inForce": "IN_FORCE", "status": "obowiązujący"}
    assert assess_act_in_force(meta, TODAY, TODAY).status == VS.NOT_APPLICABLE


def test_repealed_act_before_and_after_repeal():
    meta = {"entryIntoForce": "1996-08-26", "repealDate": "2015-10-01", "inForce": "NOT_IN_FORCE"}
    assert assess_act_in_force(meta, date(2010, 1, 1), TODAY).status == VS.VERIFIED
    assert assess_act_in_force(meta, date(2015, 10, 1), TODAY).status == VS.NOT_APPLICABLE


def test_missing_entry_date_is_unverified():
    assert assess_act_in_force({"inForce": "IN_FORCE"}, TODAY, TODAY).status == VS.UNVERIFIED


def test_not_in_force_without_end_date_is_unverified():
    meta = {"entryIntoForce": "2000-01-01", "inForce": "NOT_IN_FORCE"}
    assert assess_act_in_force(meta, TODAY, TODAY).status == VS.UNVERIFIED


def test_future_date_is_unverified():
    meta = {"entryIntoForce": "2000-01-01", "inForce": "IN_FORCE"}
    assert assess_act_in_force(meta, date(2030, 1, 1), TODAY).status == VS.UNVERIFIED


# --- provision-level --------------------------------------------------------------------------


def test_verified_only_in_the_narrow_case():
    assert assess_temporal(inputs()).status == VS.VERIFIED


def test_refuses_verified_when_changes_follow_the_source():
    result = assess_temporal(inputs(changes=[ref("DU/2025/603", date(2025, 5, 23))]))
    assert result.status == VS.UNVERIFIED
    assert "DU/2025/603" in result.detail


def test_refuses_verified_when_changes_have_no_date():
    assert assess_temporal(inputs(undated_changes=[ref("DU/2025/1", None)])).status == VS.UNVERIFIED


def test_refuses_verified_without_state_date():
    assert assess_temporal(inputs(state_date=None)).status == VS.UNVERIFIED


def test_refuses_verified_for_future_dates():
    assert assess_temporal(inputs(as_of=date(2027, 1, 1))).status == VS.UNVERIFIED


def test_refuses_verified_without_text():
    assert assess_temporal(inputs(text_read=False)).status == VS.UNVERIFIED


def test_refuses_verified_on_unresolved_staged_entry():
    staged = StagedEntry(
        clause="Ustawa wchodzi w życie ... z wyjątkiem", unresolved=True, latest_date=None
    )
    assert assess_temporal(inputs(staged=staged)).status == VS.UNVERIFIED


def test_resolved_staged_entry_in_the_past_does_not_block():
    staged = StagedEntry(clause="...", unresolved=False, latest_date=date(2015, 10, 1))
    assert assess_temporal(inputs(staged=staged)).status == VS.VERIFIED


def test_refuses_verified_when_footnote_dates_are_later():
    assert (
        assess_temporal(inputs(as_of=date(2024, 6, 1), footnote_dates=[date(2025, 1, 1)])).status
        == VS.UNVERIFIED
    )


def test_repealed_unit_is_not_applicable():
    assert assess_temporal(inputs(marker="(uchylony)")).status == VS.NOT_APPLICABLE


def test_act_not_applicable_propagates():
    act = CheckResult(status=VS.NOT_APPLICABLE, detail="uchylony")
    assert assess_temporal(inputs(act_check=act)).status == VS.NOT_APPLICABLE


def test_act_level_uncertainty_blocks_verified():
    act = CheckResult(status=VS.UNVERIFIED, detail="brak daty")
    assert assess_temporal(inputs(act_check=act)).status == VS.UNVERIFIED


def test_stem_match_needs_six_shared_letters_for_long_words():
    from polish_legal_mcp.services.legal import _stem_match

    assert not _stem_match("zachowek", "zachodniopomorskie")
    assert _stem_match("wywłaszczenie", "wywłaszczenia")
    assert _stem_match("lokalu", "lokal")


def test_stem_handles_fleeting_e():
    from polish_legal_mcp.services.legal import _stem

    assert _stem("spadek") == "spadk" and "spadk" in "o podatku od spadków i darowizn"
    assert _stem("podatek") == "podatk"
    assert _stem("wywłaszczenie") == "wywłaszcze"
