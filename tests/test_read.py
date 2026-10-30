"""Reading CAMT files: camt.053/052 in the versions 001.02 and 001.08, ZIP, CSV-CAMT."""

from __future__ import annotations

import io
import time
import zipfile
from datetime import date
from decimal import Decimal, Inexact, Rounded, localcontext

import camt_fixtures as f
import pytest

from camt_reader import CamtError, CamtKind, Entry, EntryStatus, read_file, read_upload, remittance_lines

OCT_1, OCT_2, OCT_5 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)


def _statement(version: str) -> bytes:
    return f.camt(
        "053",
        version,
        OCT_1,
        date(2026, 10, 31),
        [
            f.incoming(OCT_1, "1190.00", "RE0042", ref="2026100100001"),
            f.direct_debit(OCT_2, "49.90", "Beitrag Oktober", ref="2026100200001"),
        ],
        opening="1000.00",
    )


@pytest.mark.parametrize("version", ["02", "08"])
def test_a_statement_is_read_in_both_versions(version: str) -> None:
    file = read_file(_statement(version), "auszug.xml")
    assert (file.kind, file.version) == (CamtKind.STATEMENT, f"001.{version}")
    [report] = file.reports
    assert report.iban == f.ACCOUNT_IBAN and report.currency == "EUR"
    assert (report.period_start, report.period_end, report.complete_until) == (OCT_1, date(2026, 10, 31), date(2026, 10, 31))
    assert (report.opening_balance, report.closing_balance) == (Decimal("1000.00"), Decimal("2140.10"))
    credit, debit = report.entries
    assert (credit.status, credit.booking_date, credit.value_date, credit.amount) == (
        EntryStatus.BOOKED, OCT_1, OCT_1, Decimal("1190.00")
    )
    assert credit.reference == "2026100100001" and credit.reversal is False and not credit.is_batch
    assert credit.bank_transaction_code == "PMNT/RCDT/ESCT NTRF+166" and credit.booking_text == "SEPA-Gutschrift"
    # The counterparty of a credit is the debtor.
    detail = credit.detail
    assert (detail.counterparty_name, detail.counterparty_iban, detail.counterparty_bic) == (
        "Customer AG", f.CUSTOMER_IBAN, "CUSTDEFFXXX"
    )
    assert detail.remittance == ("RE0042",) and detail.end_to_end_id == "E2E-RE0042"
    assert detail.amount == Decimal("1190.00")
    assert remittance_lines(credit) == ["RE0042", "EREF: E2E-RE0042", "SEPA-Gutschrift"]
    # A direct debit names the creditor, the mandate and the creditor id.
    assert debit.amount == Decimal("-49.90") and debit.detail.counterparty_name == "Insurer AG"
    assert (debit.detail.mandate_id, debit.detail.creditor_id) == ("MANDAT-0815", f.CREDITOR_ID)
    assert remittance_lines(debit) == [
        "Beitrag Oktober", "EREF: POLICE-2026-10", "MREF: MANDAT-0815", f"CRED: {f.CREDITOR_ID}", "SEPA-Lastschrift"
    ]


def test_both_versions_read_the_same_entries() -> None:
    old, new = (read_file(_statement(version), "a.xml").reports[0] for version in ("02", "08"))
    assert old.entries == new.entries


def test_a_batch_booking_is_one_entry_with_its_transactions() -> None:
    batch = f.Tx(
        "300.00",
        False,
        OCT_5,
        OCT_5,
        ref="2026100500001",
        addtl="SAMMEL-UEBERWEISUNG",
        batch=[
            f.transfer(OCT_5, "100.00", "Rechnung A-1"),
            f.transfer(OCT_5, "200.00", "Rechnung B-2", name="Other GmbH", account=f.INSURER_IBAN),
        ],
    )
    [report] = read_file(f.camt("053", "08", OCT_5, OCT_5, [batch], opening="500.00"), "b.xml").reports
    [entry] = report.entries
    assert entry.is_batch and entry.batch_count == 2 and entry.amount == Decimal("-300.00")
    assert [detail.amount for detail in entry.details] == [Decimal("-100.00"), Decimal("-200.00")]
    assert [detail.counterparty_name for detail in entry.details] == ["Supplier GmbH", "Other GmbH"]
    # One transaction carries no single counterparty; its text lists the transactions.
    assert entry.detail.counterparty_name is None
    assert remittance_lines(entry) == [
        "Sammelbuchung, 2 Posten",
        "-100.00 Supplier GmbH Rechnung A-1",
        "-200.00 Other GmbH Rechnung B-2",
        "SAMMEL-UEBERWEISUNG",
    ]


def test_a_reversed_direct_debit_names_the_creditor_and_is_marked() -> None:
    returned = f.direct_debit(OCT_5, "49.90", "Beitrag Oktober", ref="R1", reversal=True)
    returned.credit = True
    [report] = read_file(f.camt("053", "02", OCT_5, OCT_5, [returned], opening="0.00"), "r.xml").reports
    [entry] = report.entries
    assert entry.reversal is True and entry.amount == Decimal("49.90")
    assert entry.detail.counterparty_name == "Insurer AG" and entry.detail.counterparty_iban == f.INSURER_IBAN


def test_an_intraday_report_keeps_pending_entries_apart_and_covers_only_whole_days() -> None:
    pending = f.incoming(OCT_5, "10.00", "RE0050", status="PDNG")
    content = f.camt("052", "08", OCT_5, OCT_5, [f.incoming(OCT_5, "20.00", "RE0049"), pending], to_time="14:30:00")
    file = read_file(content, "intraday.xml")
    assert file.kind is CamtKind.REPORT
    [report] = file.reports
    assert [entry.status for entry in report.entries] == [EntryStatus.BOOKED, EntryStatus.PENDING]
    assert report.booked == [report.entries[0]] and report.total == Decimal("20.00")
    # It ends at 14:30: no whole day.
    assert report.complete_until is None and report.opening_balance is None
    whole = read_file(f.camt("052", "08", OCT_1, OCT_5, [], to_time="23:59:59"), "r.xml").reports[0]
    assert whole.complete_until == OCT_5


def test_csv_camt_is_read_by_its_column_names() -> None:
    entries = [
        f.incoming(OCT_1, "1190.00", "RE0042"),
        f.direct_debit(OCT_2, "49.90", "Beitrag Oktober"),
        f.incoming(OCT_5, "5.00", "RE0051", status="PDNG", name="Müller & Söhne"),
    ]
    file = read_file(f.csv_camt(entries), "umsaetze.csv")
    assert (file.kind, file.version) == (CamtKind.CSV, "17 columns")
    [report] = file.reports
    assert report.iban == f.ACCOUNT_IBAN and report.opening_balance is None
    # No period of its own: the first and last booked day; it may have been exported during
    # the last one, so it is complete up to the day before.
    assert (report.period_start, report.period_end, report.complete_until) == (OCT_1, OCT_2, OCT_1)
    pending, debit, credit = report.entries
    assert pending.status is EntryStatus.PENDING and pending.detail.counterparty_name == "Müller & Söhne"
    assert (debit.booking_date, debit.amount, debit.detail.mandate_id, debit.detail.creditor_id) == (
        OCT_2, Decimal("-49.90"), "MANDAT-0815", f.CREDITOR_ID
    )
    assert credit.reference is None and credit.reversal is None
    assert credit.detail.counterparty_iban == f.CUSTOMER_IBAN and credit.booking_text == "SEPA-GUTSCHRIFT"


def test_a_csv_export_of_one_booked_day_holds_no_whole_day() -> None:
    entries = [f.incoming(OCT_2, "1.00", "A"), f.incoming(OCT_2, "2.00", "B"), f.incoming(OCT_1, "3.00", "C", status="PDNG")]
    [report] = read_file(f.csv_camt(entries), "a.csv").reports
    assert (report.period_start, report.period_end, report.complete_until) == (OCT_2, OCT_2, None)


def _identity(entry: Entry) -> tuple[object, ...]:
    """What a caller compares to recognise the same booking in another file."""
    detail = entry.detail
    return (
        entry.booking_date, entry.value_date, entry.amount, entry.currency, detail.counterparty_iban,
        detail.end_to_end_id, detail.mandate_id, detail.remittance, entry.is_batch,
    )


def test_the_same_booking_reads_the_same_in_xml_and_csv() -> None:
    entries = [f.incoming(OCT_1, "1190.00", "RE0042", ref="X1"), f.direct_debit(OCT_2, "49.90", "Beitrag Oktober")]
    xml = read_file(f.camt("053", "08", OCT_1, OCT_2, entries, opening="0.00"), "a.xml").reports[0]
    csv = read_file(f.csv_camt(entries, encoding="utf-8"), "a.csv").reports[0]
    by_day = sorted(csv.entries, key=lambda entry: entry.booking_date or OCT_1)
    assert [_identity(entry) for entry in xml.entries] == [_identity(entry) for entry in by_day]


def test_amounts_do_not_depend_on_the_decimal_context_of_the_caller() -> None:
    day = date(2026, 10, 1)
    batch = f.Tx("7654321.12", False, day, day, batch=[f.transfer(day, "7654321.12", "A")])
    entries = [f.incoming(day, "1234567.89", "B"), f.transfer(day, "1234567.88", "C"), batch]
    statement = f.camt("053", "08", day, day, entries, opening="-7654321.13")
    export = f.csv_camt(entries[:2])
    with localcontext() as context:
        context.prec = 3
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        report = read_file(statement, "a.xml").reports[0]
        lines = read_file(export, "a.csv").reports[0]
    assert report.opening_balance == Decimal("-7654321.13")
    assert report.closing_balance == Decimal("-15308642.24")
    assert [entry.amount for entry in report.entries] == [
        Decimal("1234567.89"),
        Decimal("-1234567.88"),
        Decimal("-7654321.12"),
    ]
    assert report.entries[1].details[0].amount == Decimal("-1234567.88")
    assert report.entries[2].details[0].amount == Decimal("-7654321.12")
    assert [entry.amount for entry in lines.entries] == [Decimal("1234567.89"), Decimal("-1234567.88")]


def test_a_csv_amount_of_minus_zero_has_no_negative_zero() -> None:
    day = date(2026, 10, 1)
    export = f.csv_camt([f.transfer(day, "0.00", "A"), f.transfer(day, "1.00", "B")])
    export = export.replace(b'"0,00"', b'"-0,00"')
    assert b'"-0,00"' in export
    plain = read_file(export, "a.csv").reports[0]
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        trapped = read_file(export, "a.csv").reports[0]
    for report in (plain, trapped):
        assert sorted(str(entry.amount) for entry in report.entries) == ["-1.00", "0.00"]


def test_a_debit_of_zero_keeps_its_sign_in_xml() -> None:
    day = date(2026, 10, 1)
    statement = f.camt("053", "08", day, day, [f.transfer(day, "0.00", "A")], opening="0.00")
    assert str(read_file(statement, "a.xml").reports[0].entries[0].amount) == "-0.00"


def test_a_zip_holds_several_files() -> None:
    first = f.camt("053", "08", OCT_1, OCT_1, [f.incoming(OCT_1, "1.00", "A")], opening="0.00", statement_id="1")
    second = f.camt("053", "08", OCT_2, OCT_2, [], opening="1.00", statement_id="2")
    files = read_upload(f.zipped(("2026-10-02.xml", second), ("2026-10-01.xml", first)), "auszuege.zip")
    assert [file.name for file in files] == ["2026-10-01.xml", "2026-10-02.xml"]
    assert len({file.sha256 for file in files}) == 2


@pytest.mark.parametrize(
    ("content", "message"),
    [
        pytest.param(b"", "empty", id="empty"),
        pytest.param(f.zipped(("readme.txt", b"hello")), "no XML file", id="zip-without-xml"),
        pytest.param(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>', "document type", id="doctype"),
        pytest.param(
            b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.054.001.02"/>', "not a camt.052 or camt.053", id="camt054"
        ),
        pytest.param(b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.04"/>', "is not read", id="camt053-v04"),
        pytest.param(
            b'"Auftragskonto";"Buchungstag";"Betrag"\r\n"DE";"01.10.26";"1,00"\r\n', "not a CSV-CAMT export", id="foreign-csv"
        ),
        pytest.param(f.camt("053", "08", OCT_1, OCT_1, []), "needs its opening", id="statement-without-opening"),
    ],
)
def test_what_is_not_read_is_refused(content: bytes, message: str) -> None:
    with pytest.raises(CamtError, match=message):
        read_upload(content, "file")


def test_a_zip_does_not_depend_on_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    members = (("a.xml", b"<x/>"), ("b.txt", b"hello"))
    first = f.zipped(*members)
    monkeypatch.setattr(time, "time", lambda: 2_000_000_000.0)
    monkeypatch.setattr(time, "localtime", lambda *_: time.gmtime(2_000_000_000))
    assert f.zipped(*members) == first
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert {info.date_time for info in archive.infolist()} == {f.ZIP_MEMBER_TIME}
        assert {info.compress_type for info in archive.infolist()} == {zipfile.ZIP_DEFLATED}


def test_a_csv_with_two_accounts_is_refused() -> None:
    content = f.csv_camt([f.incoming(OCT_1, "1.00", "A")]) + f.csv_camt(
        [f.incoming(OCT_1, "1.00", "B")], account=f.OTHER_ACCOUNT_IBAN
    ).split(b"\r\n", 1)[1]
    with pytest.raises(CamtError, match="several accounts"):
        read_file(content, "a.csv")
