"""The neutral model of what a CAMT file says, the limits of reading one, and its one error."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

_MIB = 1024 * 1024
#: A reference that is none.
_NO_REFERENCE = frozenset({"", "NOTPROVIDED", "NONREF", "NOT PROVIDED"})


class CamtError(ValueError):
    """The file is no CAMT file this reader takes, or it cannot be read unambiguously.

    The only exception the reader raises for its input, whatever the input is. Its message
    names the place (``entry 3``, ``line 7``, ``ZIP member 2``) but never quotes amounts,
    names, accounts, remittance text or file names of a ZIP, nor does its traceback, so it
    can be logged.
    """


@dataclass(frozen=True, slots=True)
class Limits:
    """What the reader takes at most. Anything beyond a limit is a :class:`CamtError`.

    The defaults are safe for untrusted input and fit bank statements of any usual size;
    raise them only for sources you trust.
    """

    #: The bytes of one file: an upload, or one member of a ZIP.
    max_file_size: int = 50 * _MIB
    #: The files in one ZIP.
    max_members: int = 400
    #: The bytes all members of one ZIP unpack to.
    max_unpacked: int = 50 * _MIB
    #: Unpacked bytes per compressed byte of one ZIP member.
    max_ratio: int = 200
    #: How deep XML elements may nest.
    max_depth: int = 32
    #: The XML elements in one file.
    max_elements: int = 2_000_000


class CamtKind(StrEnum):
    #: camt.053: the account statement, with opening and closing balance.
    STATEMENT = "camt.053"
    #: camt.052: the intraday account report.
    REPORT = "camt.052"
    #: The Sparkasse online-banking export „CSV-CAMT“.
    CSV = "csv-camt"


class EntryStatus(StrEnum):
    BOOKED = "BOOK"
    PENDING = "PDNG"
    INFO = "INFO"


@dataclass(frozen=True, slots=True)
class Detail:
    """One transaction of an entry (``TxDtls``; a CSV row has exactly one)."""

    #: Signed like the entry; ``None`` if the file does not state it per transaction.
    amount: Decimal | None = None
    counterparty_name: str | None = None
    counterparty_iban: str | None = None
    counterparty_bic: str | None = None
    #: The unstructured remittance lines (``Ustrd``), as written.
    remittance: tuple[str, ...] = ()
    #: The structured creditor reference (``Strd/CdtrRefInf/Ref``).
    structured_reference: str | None = None
    end_to_end_id: str | None = None
    mandate_id: str | None = None
    #: The SEPA creditor identifier of a direct debit (Gläubiger-ID).
    creditor_id: str | None = None
    #: The bank's reference of the transaction (``Refs/AcctSvcrRef``).
    reference: str | None = None
    bank_transaction_code: str | None = None
    additional_info: str | None = None


@dataclass(frozen=True, slots=True)
class Entry:
    """One entry (``Ntry``; a CSV row) — one movement of money on the account."""

    #: 1-based position in its report.
    position: int
    status: EntryStatus
    booking_date: date | None
    value_date: date | None
    #: Signed: incoming > 0.
    amount: Decimal
    currency: str
    reversal: bool | None
    #: The bank's reference of the entry (``AcctSvcrRef``), if it states one.
    reference: str | None
    bank_transaction_code: str | None
    #: ``AddtlNtryInf`` / the CSV's booking text (``Buchungstext``).
    booking_text: str | None
    details: tuple[Detail, ...] = ()
    #: The number of transactions a batch booking states (``Btch/NbOfTxs``).
    batch_count: int | None = None
    #: The batch reference of a CSV row (``Sammlerreferenz``).
    batch_reference: str | None = None

    @property
    def is_batch(self) -> bool:
        """Several transactions booked as one entry."""
        return len(self.details) > 1 or (self.batch_count or 0) > 1 or bool(self.batch_reference)

    @property
    def detail(self) -> Detail:
        """The entry's one transaction (empty for a batch or an entry without details)."""
        return self.details[0] if len(self.details) == 1 and not self.is_batch else Detail()

    @property
    def label(self) -> str:
        """``entry 3 (01.10.2026, -19.99 EUR)`` — how a message names it."""
        day = f"{self.booking_date:%d.%m.%Y}, " if self.booking_date else ""
        return f"entry {self.position} ({day}{self.amount:+} {self.currency})"


@dataclass(frozen=True, slots=True)
class Report:
    """One statement (``Stmt``), report (``Rpt``) or CSV export: the entries of one account."""

    iban: str
    currency: str | None
    #: The period the file states (the CSV: its first and last booking date).
    period_start: date
    period_end: date
    #: The last day whose entries are all in the file: the period end, for an intraday
    #: report that ends during the day and for a CSV export (it states no period, it may
    #: have been downloaded during its last booked day) the day before (``None``: no whole day).
    complete_until: date | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    entries: tuple[Entry, ...]
    #: The bank's id of the statement (``Stmt/Id``) and its sequence numbers.
    identifier: str | None = None
    sequence: str | None = None

    @property
    def booked(self) -> list[Entry]:
        return [entry for entry in self.entries if entry.status is EntryStatus.BOOKED]

    @property
    def total(self) -> Decimal:
        """The sum of the booked entries."""
        return sum((entry.amount for entry in self.booked), Decimal(0))


@dataclass(frozen=True, slots=True)
class CamtFile:
    """One file of an upload (a ZIP holds several)."""

    name: str
    sha256: str
    kind: CamtKind
    #: ``001.02``, ``001.08``; for CSV-CAMT the number of columns it has, e.g. ``17 columns``.
    version: str
    reports: tuple[Report, ...] = field(default=())

    @property
    def period_start(self) -> date:
        return min(report.period_start for report in self.reports)

    @property
    def period_end(self) -> date:
        return max(report.period_end for report in self.reports)


def remittance_lines(entry: Entry) -> list[str]:
    """The text of an entry as a bank transaction carries it (German conventions):
    the remittance lines, the structured reference, ``EREF:``/``MREF:``/``CRED:``, the
    booking text — or, for a batch, a line per transaction."""
    if entry.is_batch:
        count = entry.batch_count or len(entry.details)
        lines = [f"Sammelbuchung, {count} Posten" if count else "Sammelbuchung"]
        if entry.batch_reference:
            lines.append(f"Sammlerreferenz: {entry.batch_reference}")
        lines.extend(_batch_line(detail) for detail in entry.details)
        if entry.booking_text:
            lines.append(entry.booking_text)
        return lines
    detail = entry.detail
    lines = list(detail.remittance)
    if detail.structured_reference:
        lines.append(detail.structured_reference)
    for label, value in (("EREF", detail.end_to_end_id), ("MREF", detail.mandate_id), ("CRED", detail.creditor_id)):
        if value:
            lines.append(f"{label}: {value}")
    for extra in (detail.additional_info, entry.booking_text):
        if extra and extra not in lines:
            lines.append(extra)
    return lines


def _batch_line(detail: Detail) -> str:
    parts = [f"{detail.amount:+}" if detail.amount is not None else "", detail.counterparty_name or ""]
    text = " ".join([*detail.remittance, detail.structured_reference or ""]).strip()
    return " ".join(part for part in (*parts, text) if part)


def reference(value: str | None) -> str | None:
    """``value``, unless it is one of the words banks write for "no reference"."""
    return None if value is None or value.upper() in _NO_REFERENCE else value
