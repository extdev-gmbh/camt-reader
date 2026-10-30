"""The round trip with the writer of fints2camt: what it writes, this library reads.

The fixture is its real output for invented data (scripts/make_fints2camt_fixture.py),
validated there against the XSD of camt.053.001.02.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from camt_reader import CamtKind, Report, read_upload, remittance_lines

FIXTURE = Path(__file__).parent / "fixtures" / "fints2camt_synthetic.xml"


def _report() -> Report:
    [file] = read_upload(FIXTURE.read_bytes(), FIXTURE.name)
    assert (file.kind, file.version) == (CamtKind.STATEMENT, "001.02")
    [report] = file.reports
    return report


def test_the_statement_and_its_balances() -> None:
    report = _report()
    assert (report.iban, report.currency) == ("DE89370400440532013000", "EUR")
    assert (report.period_start, report.period_end) == (date(2026, 10, 1), date(2026, 10, 3))
    assert report.complete_until == date(2026, 10, 3)
    assert (report.opening_balance, report.closing_balance) == (Decimal("-200.00"), Decimal("690.00"))
    assert report.opening_balance + report.total == report.closing_balance
    assert report.identifier and len(report.entries) == len(report.booked) == 4


def test_a_credit_with_an_umlaut_in_its_counterparty() -> None:
    entry = _report().entries[0]
    assert (entry.amount, entry.reference, entry.reversal) == (Decimal("1190.00"), "2026100100001", False)
    assert entry.bank_transaction_code == "PMNT/RCDT/ESCT NTRF+166"
    assert (entry.detail.counterparty_name, entry.detail.counterparty_iban, entry.detail.counterparty_bic) == (
        "Müller & Söhne KG", "FR1420041010050500013M02606", "CUSTDEFFXXX"
    )
    assert remittance_lines(entry) == ["RE0042 Kundennummer 4711", "EREF: E2E-RE0042", "SEPA-Gutschrift"]


def test_a_direct_debit_with_mandate_creditor_id_and_chunked_remittance() -> None:
    entry = _report().entries[1]
    assert entry.amount == Decimal("-49.90") and entry.value_date == date(2026, 10, 2)
    detail = entry.detail
    assert detail.counterparty_name == "Insurer AG"
    assert (detail.mandate_id, detail.creditor_id) == ("MANDAT-0815", "DE98ZZZ09999999999")
    # The writer cuts a long remittance into lines of at most 140 characters.
    assert len(detail.remittance) > 1 and all(len(line) <= 140 for line in detail.remittance)


def test_a_reversal_keeps_its_counterparty() -> None:
    entry = _report().entries[2]
    assert entry.reversal is True and entry.amount == Decimal("49.90")
    assert entry.detail.counterparty_iban == "BE68539007547034"


def test_a_batch_and_an_account_without_iban() -> None:
    entry = _report().entries[3]
    assert entry.is_batch and entry.batch_count == 2 and entry.amount == Decimal("-300.00")
    assert [detail.counterparty_name for detail in entry.details] == ["Supplier GmbH", "Other GmbH"]
    assert [detail.counterparty_iban for detail in entry.details] == ["NL91ABNA0417164300", None]
