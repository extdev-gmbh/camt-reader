"""Write tests/fixtures/fints2camt_synthetic.xml with the camt.053 writer of fints2camt.

Not part of the gate (fints2camt is no dependency of this library): run it by hand after a
change of that writer, with fints2camt installed or on the path, and commit the result.

    python scripts/make_fints2camt_fixture.py tests/fixtures/fints2camt_synthetic.xml

All data is invented; the IBANs are the example IBANs of ISO 13616.
"""

from __future__ import annotations

import datetime
import sys
from decimal import Decimal
from pathlib import Path

from fints2camt.camt import render_camt053, validate_camt053
from fints2camt.model import (
    Balance,
    BankTransactionCode,
    Entry,
    FetchWindow,
    Party,
    Statement,
    TransactionDetails,
    assign_entry_references,
)

OWN_IBAN = "DE89370400440532013000"
START, END = datetime.date(2026, 10, 1), datetime.date(2026, 10, 3)
SEPA_CREDIT = BankTransactionCode("PMNT", "RCDT", "ESCT", "NTRF+166", "DK")
SEPA_DEBIT = BankTransactionCode("PMNT", "IDDT", "ESDD", "NDDT+105", "DK")
OWNER = Party(name="Beispiel GmbH", iban=OWN_IBAN)


def _entries() -> list[Entry]:
    return [
        Entry(
            amount=Decimal("1190.00"), currency="EUR", credit=True, booking_date=START, value_date=START,
            account_servicer_reference="2026100100001", bank_transaction_code=SEPA_CREDIT,
            additional_info="SEPA-Gutschrift",
            transactions=[TransactionDetails(
                end_to_end_id="E2E-RE0042", debtor=Party(name="Müller & Söhne KG", iban="FR1420041010050500013M02606", bic="CUSTDEFFXXX"),
                creditor=OWNER, remittance=["RE0042 Kundennummer 4711"],
            )],
        ),
        Entry(
            amount=Decimal("49.90"), currency="EUR", credit=False, booking_date=START,
            value_date=datetime.date(2026, 10, 2), bank_transaction_code=SEPA_DEBIT,
            additional_info="SEPA-Lastschrift",
            transactions=[TransactionDetails(
                end_to_end_id="POLICE-2026-10", mandate_id="MANDAT-0815", debtor=OWNER,
                creditor=Party(name="Insurer AG", iban="BE68539007547034"), creditor_id="DE98ZZZ09999999999",
                remittance=["Beitrag Oktober " + "lang " * 40],
            )],
        ),
        Entry(
            amount=Decimal("49.90"), currency="EUR", credit=True, booking_date=datetime.date(2026, 10, 2),
            reversal=True, bank_transaction_code=SEPA_DEBIT,
            transactions=[TransactionDetails(
                debtor=OWNER, creditor=Party(name="Insurer AG", iban="BE68539007547034"),
                remittance=["Ruecklastschrift"],
            )],
        ),
        Entry(
            amount=Decimal("300.00"), currency="EUR", credit=False, booking_date=END, value_date=END,
            bank_transaction_code=BankTransactionCode(proprietary="NMSC+191"), batch=True, batch_count=2,
            transactions=[
                TransactionDetails(creditor=Party(name="Supplier GmbH", iban="NL91ABNA0417164300"), remittance=["Rechnung A-1"]),
                TransactionDetails(creditor=Party(name="Other GmbH", other_account="1234567890", bank_code="10010010"), remittance=["Rechnung B-2"]),
            ],
        ),
    ]


def main(path: str) -> None:
    entries = _entries()
    assign_entry_references(OWN_IBAN, entries)
    statement = Statement(
        iban=OWN_IBAN,
        currency="EUR",
        window=FetchWindow(START, END),
        opening=Balance(Decimal("-200.00"), "EUR", START),
        closing=Balance(Decimal("690.00"), "EUR", END),
        entries=entries,
        bic="TESTDEFFXXX",
    )
    document = render_camt053(statement, datetime.datetime(2026, 10, 4, 6, 0, 0))
    validate_camt053(document)
    Path(path).write_bytes(document)


if __name__ == "__main__":
    main(sys.argv[1])
