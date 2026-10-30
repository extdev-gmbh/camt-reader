"""CSV-CAMT: the Sparkasse online-banking export „CSV-CAMT V2“ / „CSV-CAMT V8“.

Semicolon-separated, quoted, one header line, Windows-1252 or UTF-8. The columns are read
by their header names (:data:`_CSV_COLUMNS`), so their order and extra columns do not
matter; the columns that make it CSV-CAMT and not CSV-MT940 — the end-to-end reference and
the mandate reference — must be there. The export carries neither a period nor balances nor
the bank's entry reference.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

from ._iban import is_valid_iban, normalise_iban
from ._model import CamtError, Detail, Entry, EntryStatus, Report, reference

_AMOUNT = re.compile(r"([+-]?)([0-9]{1,3}(?:\.[0-9]{3}){1,4}|[0-9]{1,15})(?:,([0-9]{1,2}))?")

#: Field → the header names it is read from (as :func:`_column_key` writes them).
_CSV_COLUMNS: dict[str, tuple[str, ...]] = {
    "account": ("auftragskonto",),
    "booking_date": ("buchungstag",),
    "value_date": ("valutadatum", "wertstellung"),
    "booking_text": ("buchungstext",),
    "remittance": ("verwendungszweck",),
    "creditor_id": ("glaeubigerid",),
    "mandate_id": ("mandatsreferenz",),
    "end_to_end_id": ("kundenreferenzendtoend", "kundenreferenz"),
    "batch_reference": ("sammlerreferenz",),
    "name": ("beguenstigterzahlungspflichtiger",),
    "iban": ("kontonummeriban", "iban"),
    "bic": ("bicswiftcode", "bic"),
    "amount": ("betrag",),
    "currency": ("waehrung",),
    "info": ("info",),
}
_CSV_REQUIRED = ("account", "booking_date", "amount", "currency", "end_to_end_id", "mandate_id")
#: More columns than any export has: a header that long is not worth normalising cell by cell.
_MAX_COLUMNS = 200
#: The ``Info`` of a row that is not booked yet.
_PENDING_INFO = ("vorgemerkt", "nicht gebucht")


def _column_key(header: str) -> str:
    """A header name without case, umlauts, blanks and punctuation: ``Kontonummer/IBAN`` → ``kontonummeriban``."""
    value = header.strip().lower()
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        value = value.replace(umlaut, plain)
    return re.sub(r"[^a-z0-9]", "", value)


def _decode(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        return content[3:].decode("utf-8")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="strict")


def _date(value: str, what: str) -> date:
    for pattern in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise CamtError(f"{what}: not a date")


def _amount(value: str, what: str) -> Decimal:
    match = _AMOUNT.fullmatch(value.replace(" ", ""))
    if match is None:
        raise CamtError(f"{what}: not an amount")
    sign, whole, cents = match.groups()
    amount = Decimal(f"{whole.replace('.', '')}.{(cents or '0').ljust(2, '0')}")
    # No negative zero: an amount of "-0,00" reads as 0.00. Callers hash the string form of amounts.
    return amount.copy_negate() if sign == "-" and amount else amount


def _rows(content: bytes) -> list[list[str]]:
    try:
        text = _decode(content)
    except UnicodeDecodeError as exc:
        raise CamtError("the file is neither XML nor a text file") from exc
    try:
        return [row for row in csv.reader(io.StringIO(text, newline=""), delimiter=";") if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        # Its message names no content; among others a cell beyond csv.field_size_limit().
        raise CamtError(f"the CSV cannot be read: {exc}") from exc


def read_csv(content: bytes) -> tuple[str, list[Report]]:
    rows = _rows(content)
    if not rows:
        raise CamtError("the file holds no line")
    if len(rows[0]) > _MAX_COLUMNS:
        raise CamtError(f"the header has {len(rows[0])} columns, at most {_MAX_COLUMNS} are taken")
    keys = [_column_key(cell) for cell in rows[0]]
    columns: dict[str, int] = {}
    for name, accepted in _CSV_COLUMNS.items():
        at = next((keys.index(key) for key in accepted if key in keys), None)
        if at is not None:
            columns[name] = at
    missing = [name for name in _CSV_REQUIRED if name not in columns]
    if missing:
        raise CamtError(
            "not a CSV-CAMT export (columns missing: "
            f"{', '.join(_CSV_COLUMNS[name][0] for name in missing)}); export the turnover as CSV-CAMT"
        )

    def cell(row: Sequence[str], name: str) -> str | None:
        at = columns.get(name)
        if at is None or at >= len(row):
            return None
        return " ".join(row[at].split()) or None

    entries: list[Entry] = []
    ibans: set[str] = set()
    for position, row in enumerate(rows[1:], start=1):
        what = f"line {position + 1}"
        if len(row) != len(keys):
            raise CamtError(f"{what}: {len(row)} columns, the header has {len(keys)}")
        account = cell(row, "account")
        if account is None:
            raise CamtError(f"{what}: no account (Auftragskonto)")
        ibans.add(normalise_iban(account))
        currency = (cell(row, "currency") or "").upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise CamtError(f"{what}: no currency")
        info = (cell(row, "info") or "").lower()
        status = EntryStatus.PENDING if any(word in info for word in _PENDING_INFO) else EntryStatus.BOOKED
        value_date = cell(row, "value_date")
        counterparty_iban = cell(row, "iban")
        remittance = cell(row, "remittance")
        detail = Detail(
            counterparty_name=cell(row, "name"),
            counterparty_iban=normalise_iban(counterparty_iban) if counterparty_iban else None,
            counterparty_bic=cell(row, "bic"),
            remittance=(remittance,) if remittance else (),
            end_to_end_id=reference(cell(row, "end_to_end_id")),
            mandate_id=reference(cell(row, "mandate_id")),
            creditor_id=reference(cell(row, "creditor_id")),
        )
        entries.append(
            Entry(
                position=position,
                status=status,
                booking_date=_date(cell(row, "booking_date") or "", what),
                value_date=_date(value_date, what) if value_date else None,
                amount=_amount(cell(row, "amount") or "", what),
                currency=currency,
                reversal=None,
                reference=None,
                bank_transaction_code=None,
                booking_text=cell(row, "booking_text"),
                details=(detail,),
                batch_reference=reference(cell(row, "batch_reference")),
            )
        )
    if len(ibans) > 1:
        raise CamtError("the export holds several accounts")
    if not entries:
        raise CamtError("the export holds no entry")
    (iban,) = ibans
    if not is_valid_iban(iban):
        raise CamtError("invalid account IBAN (Auftragskonto)")
    booked = [entry.booking_date for entry in entries if entry.status is EntryStatus.BOOKED and entry.booking_date]
    if not booked:
        raise CamtError("the export holds no booked entry")
    currencies = {entry.currency for entry in entries}
    # Bookings of the last booked day may have come after the download.
    first, last = min(booked), max(booked)
    report = Report(
        iban=iban,
        currency=currencies.pop() if len(currencies) == 1 else None,
        period_start=first,
        period_end=last,
        complete_until=last - timedelta(days=1) if last > first else None,
        opening_balance=None,
        closing_balance=None,
        entries=tuple(entries),
    )
    return f"{len(keys)} columns", [report]
