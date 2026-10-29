from datetime import date

from polish_legal_mcp.parsers.entry_into_force import find_staged_entry, parse_dates


def test_parse_polish_dates():
    assert parse_dates("z dniem 1 października 2015 r. i 29 lutego 2024 r.") == [
        date(2015, 10, 1),
        date(2024, 2, 29),
    ]


def test_no_exception_means_no_staged_entry():
    assert (
        find_staged_entry("Art. 5. Ustawa wchodzi w życie z dniem 1 stycznia 2020 r.", None) is None
    )


def test_relative_period_resolved_from_promulgation():
    text = (
        "Art. 138. Ustawa wchodzi w życie z dniem 1 października 2015 r., z wyjątkiem art. 62, "
        "który wchodzi w życie po upływie 14 dni od dnia ogłoszenia."
    )
    staged = find_staged_entry(text, date(2015, 9, 7))
    assert staged is not None and not staged.unresolved
    assert staged.latest_date == date(2015, 10, 1)


def test_unparseable_period_is_unresolved():
    text = (
        "Art. 84. Ustawa wchodzi w życie pierwszego dnia miesiąca następującego po miesiącu "
        "ogłoszenia, z wyjątkiem art. 47, który wchodzi w życie z dniem ogłoszenia. Art. 85. x"
    )
    staged = find_staged_entry(text, date(2024, 6, 1))
    assert staged is not None and staged.unresolved


def test_relative_period_without_promulgation_is_unresolved():
    text = "Ustawa wchodzi w życie z dniem 1 stycznia 2020 r., z wyjątkiem art. 3, który wchodzi w życie po upływie 3 miesięcy od dnia ogłoszenia."
    staged = find_staged_entry(text, None)
    assert staged is not None and staged.unresolved
