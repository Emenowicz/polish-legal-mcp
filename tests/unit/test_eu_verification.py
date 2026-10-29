"""EU verification rules (synthetic metadata, labelled as such)."""

from dataclasses import replace
from datetime import date

from polish_legal_mcp.models import CheckResult
from polish_legal_mcp.models import VerificationStatus as VS
from polish_legal_mcp.parsers.eu_html import OjReference
from polish_legal_mcp.services.eu_law import (
    EuTemporalInputs,
    assess_eu_act_in_force,
    assess_eu_temporal,
)

TODAY = date(2026, 10, 29)
RODO_FACTS = {
    "resource_legal_date_entry-into-force": ["2016-05-24", "2018-05-25"],
    "resource_legal_date_end-of-validity": ["9999-12-31"],
    "resource_legal_in-force": ["1"],
}


def test_entry_into_force_vs_application_date():
    assert assess_eu_act_in_force(RODO_FACTS, date(2016, 1, 1), TODAY).status == VS.NOT_APPLICABLE
    assert assess_eu_act_in_force(RODO_FACTS, date(2017, 1, 1), TODAY).status == VS.UNVERIFIED
    assert assess_eu_act_in_force(RODO_FACTS, TODAY, TODAY).status == VS.VERIFIED


def test_end_of_validity():
    facts = {**RODO_FACTS, "resource_legal_date_end-of-validity": ["2020-12-31"]}
    assert assess_eu_act_in_force(facts, TODAY, TODAY).status == VS.NOT_APPLICABLE


BASE = EuTemporalInputs(
    as_of=TODAY,
    today=TODAY,
    act_check=CheckResult(status=VS.VERIFIED, detail="ok"),
    text_read=True,
    consolidated=True,
    source_date=date(2026, 1, 1),
)


def test_verified_only_when_corrigenda_and_amendments_are_covered():
    assert assess_eu_temporal(BASE).status == VS.VERIFIED
    assert (
        assess_eu_temporal(replace(BASE, missing_corrigenda=["32016R0679R(02)"])).status
        == VS.UNVERIFIED
    )
    lag = assess_eu_temporal(replace(BASE, later_amendments=["32026L0806 (od 2026-05-10)"]))
    assert lag.status == VS.UNVERIFIED and "32026L0806" in lag.detail
    assert assess_eu_temporal(replace(BASE, as_of=date(2027, 1, 1))).status == VS.UNVERIFIED
    assert assess_eu_temporal(replace(BASE, text_read=False)).status == VS.UNVERIFIED


def test_journal_name_before_2003():
    assert (
        OjReference("L 145", "43", "31.5.2001").format() == "Dz. Urz. WE L 145 z 31.5.2001, str. 43"
    )
    assert OjReference("L 119", "1", "4.5.2016").format() == "Dz. Urz. UE L 119 z 4.5.2016, str. 1"
