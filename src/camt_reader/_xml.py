"""camt.053 / camt.052 (ISO 20022 XML).

DK spec „Anlage 3, Datenformate“; versions ``001.02`` and ``001.08``, which differ in where
a party's name, a BIC and a transaction's amount stand and in how the entry status is
written — both are read the same way here::

    Document / BkToCstmrStmt / Stmt*        (camt.053, the statement)
    Document / BkToCstmrAcctRpt / Rpt*      (camt.052, the intraday report)
      Acct/Id/IBAN, Acct/Ccy, FrToDt, Bal* (OPBD/PRCD = opening, CLBD = closing)
      Ntry*: Amt, CdtDbtInd, RvslInd, Sts, BookgDt, ValDt, AcctSvcrRef, BkTxCd,
             AddtlNtryInf, NtryDtls/Btch/NbOfTxs, NtryDtls/TxDtls*
        TxDtls: Refs (AcctSvcrRef, EndToEndId, MndtId), Amt or AmtDtls/TxAmt/Amt,
                RltdPties (Dbtr, DbtrAcct, Cdtr, CdtrAcct; the creditor id of a direct
                debit as Cdtr … Othr with SchmeNm/Prtry SEPA), RltdAgts (BIC, BICFI),
                RmtInf (Ustrd*, Strd/CdtrRefInf/Ref), BkTxCd, AddtlTxInf

The document goes through expat with handlers of our own (:func:`_parse`) instead of
``defusedxml``: a document type declaration is refused the moment the parser meets it,
whatever the encoding of the file, so there are no entities and no external references, and
the same handlers enforce the depth and element limits, which ``defusedxml`` does not have.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from decimal import Decimal
from xml.etree.ElementTree import Element
from xml.parsers.expat import ExpatError, ParserCreate

from ._iban import is_valid_iban, normalise_iban
from ._model import CamtError, CamtKind, Detail, Entry, EntryStatus, Limits, Report, reference

#: The XML versions read (DK spec).
VERSIONS = ("02", "08")

_NAMESPACE = re.compile(r"\{urn:iso:std:iso:20022:tech:xsd:camt\.(052|053)\.001\.([0-9]{2})\}Document")
_AMOUNT = re.compile(r"[0-9]{1,15}(?:\.[0-9]{1,5})?")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_COUNT = re.compile(r"[0-9]{1,9}")
_END_OF_DAY = (23, 59)


def _qualified(name: str) -> str:
    """expat's ``uri}local`` (namespace separator ``}``) as ElementTree's ``{uri}local``."""
    return f"{{{name}" if "}" in name else name


def _refuse_document_type(*_: object) -> None:
    raise CamtError("the XML declares a document type; that is not taken")


def _parse(content: bytes, limits: Limits) -> Element:
    builder = ET.TreeBuilder()
    depth = elements = 0

    def start(tag: str, attributes: dict[str, str]) -> None:
        nonlocal depth, elements
        depth += 1
        elements += 1
        if depth > limits.max_depth:
            raise CamtError(f"the XML nests deeper than {limits.max_depth} elements")
        if elements > limits.max_elements:
            raise CamtError(f"the XML holds more than {limits.max_elements} elements")
        builder.start(_qualified(tag), {_qualified(key): value for key, value in attributes.items()})

    def end(tag: str) -> None:
        nonlocal depth
        depth -= 1
        builder.end(_qualified(tag))

    parser = ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    parser.StartDoctypeDeclHandler = _refuse_document_type
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(content, True)
    except CamtError:
        raise
    except ExpatError as exc:
        raise CamtError(f"the XML cannot be read: {exc}") from exc
    except (LookupError, ValueError):
        # An encoding declaration that names no usable codec.
        raise CamtError("the XML cannot be read: unknown encoding") from None
    return builder.close()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kids(element: Element | None, name: str) -> list[Element]:
    if element is None:
        return []
    return [child for child in element if _local(child.tag) == name]


def _kid(element: Element | None, *path: str) -> Element | None:
    for name in path:
        found = _kids(element, name)
        if not found:
            return None
        element = found[0]
    return element


def _text(element: Element | None, *path: str) -> str | None:
    node = _kid(element, *path)
    if node is None or node.text is None:
        return None
    value = " ".join(node.text.split())
    return value or None


def _iso_date(value: str, what: str) -> date:
    day = value[:10]
    try:
        if _DAY.fullmatch(day):
            return date.fromisoformat(day)
    except ValueError:
        raise CamtError(f"{what}: not a date") from None
    raise CamtError(f"{what}: not a date")


def _date_of(element: Element | None, what: str) -> date | None:
    """The day of a ``Dt``/``DtTm`` choice (as the bank wrote it, its local day)."""
    value = _text(element, "Dt") or _text(element, "DtTm")
    return _iso_date(value, what) if value is not None else None


def _amount(element: Element | None, what: str) -> tuple[Decimal, str]:
    if element is None or element.text is None:
        raise CamtError(f"{what}: no amount")
    text = element.text.strip()
    if not _AMOUNT.fullmatch(text):
        raise CamtError(f"{what}: not an amount")
    currency = (element.get("Ccy") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise CamtError(f"{what}: amount without currency")
    return Decimal(text), currency


def _sign(element: Element | None, what: str) -> int:
    indicator = _text(element, "CdtDbtInd")
    if indicator == "CRDT":
        return 1
    if indicator == "DBIT":
        return -1
    raise CamtError(f"{what}: the credit/debit indicator is neither CRDT nor DBIT")


def _bank_code(element: Element | None) -> str | None:
    """``PMNT/RCDT/ESCT NTRF+166`` — the ISO domain code and the bank's proprietary code."""
    if element is None:
        return None
    parts = []
    domain = _text(element, "Domn", "Cd")
    if domain:
        family = [domain, _text(element, "Domn", "Fmly", "Cd"), _text(element, "Domn", "Fmly", "SubFmlyCd")]
        parts.append("/".join(part for part in family if part))
    proprietary = _text(element, "Prtry", "Cd")
    if proprietary:
        parts.append(proprietary)
    return " ".join(parts)[:100] or None


def _party_name(party: Element | None) -> str | None:
    # 001.08 nests the party in Pty; 001.02 has the name directly.
    return _text(party, "Nm") or _text(party, "Pty", "Nm")


def _account_id(account: Element | None) -> str | None:
    iban = _text(account, "Id", "IBAN")
    return normalise_iban(iban) if iban else None


def _bic(agent: Element | None) -> str | None:
    return _text(agent, "FinInstnId", "BICFI") or _text(agent, "FinInstnId", "BIC")


def _creditor_id(creditor: Element | None) -> str | None:
    """The SEPA creditor identifier: an ``Othr`` id of the creditor under the scheme ``SEPA``."""
    if creditor is None:
        return None
    for other in creditor.iter():
        if _local(other.tag) == "Othr" and (_text(other, "SchmeNm", "Prtry") or "").upper() == "SEPA":
            return _text(other, "Id")
    return None


def _detail(tx: Element, sign: int, reversal: bool, own_iban: str, what: str) -> Detail:
    refs = _kid(tx, "Refs")
    # 001.08 states the amount directly, 001.02 in AmtDtls (an element without children is falsy: no ``or``).
    amount_node = _kid(tx, "Amt")
    if amount_node is None:
        amount_node = _kid(tx, "AmtDtls", "TxAmt", "Amt")
    amount: Decimal | None = None
    if amount_node is not None:
        value, _currency = _amount(amount_node, what)
        detail_sign = _sign(tx, what) if _text(tx, "CdtDbtInd") else sign
        amount = value.copy_sign(detail_sign)
    parties = _kid(tx, "RltdPties")
    agents = _kid(tx, "RltdAgts")
    debtor = (_party_name(_kid(parties, "Dbtr")), _account_id(_kid(parties, "DbtrAcct")), _bic(_kid(agents, "DbtrAgt")))
    creditor = (_party_name(_kid(parties, "Cdtr")), _account_id(_kid(parties, "CdtrAcct")), _bic(_kid(agents, "CdtrAgt")))
    # The other side pays in on a credit, receives on a debit; a reversal turns it round.
    incoming = (sign > 0) != reversal
    counterparty, other = (debtor, creditor) if incoming else (creditor, debtor)
    if counterparty[1] is not None and counterparty[1] == own_iban:
        counterparty = other
    remittance = _kid(tx, "RmtInf")
    lines = tuple(line for node in _kids(remittance, "Ustrd") if (line := " ".join((node.text or "").split())))
    structured = None
    for strd in _kids(remittance, "Strd"):
        structured = structured or _text(strd, "CdtrRefInf", "Ref")
    return Detail(
        amount=amount,
        counterparty_name=counterparty[0],
        counterparty_iban=counterparty[1],
        counterparty_bic=counterparty[2],
        remittance=lines,
        structured_reference=structured,
        end_to_end_id=reference(_text(refs, "EndToEndId")),
        mandate_id=reference(_text(refs, "MndtId")),
        creditor_id=_creditor_id(_kid(parties, "Cdtr")),
        reference=reference(_text(refs, "AcctSvcrRef")),
        bank_transaction_code=_bank_code(_kid(tx, "BkTxCd")),
        additional_info=_text(tx, "AddtlTxInf"),
    )


def _entry(position: int, ntry: Element, own_iban: str) -> Entry:
    what = f"entry {position}"
    value, currency = _amount(_kid(ntry, "Amt"), what)
    sign = _sign(ntry, what)
    reversal_text = _text(ntry, "RvslInd")
    if reversal_text not in (None, "true", "false"):
        raise CamtError(f"{what}: the reversal indicator is neither true nor false")
    reversal = reversal_text == "true"
    # 001.08 writes the status as Sts/Cd, 001.02 as the text of Sts.
    status_text = _text(ntry, "Sts", "Cd") or _text(ntry, "Sts")
    try:
        status = EntryStatus(status_text or "")
    except ValueError:
        raise CamtError(f"{what}: the status is none of BOOK, PDNG, INFO") from None
    booking_date = _date_of(_kid(ntry, "BookgDt"), what)
    if status is EntryStatus.BOOKED and booking_date is None:
        raise CamtError(f"{what}: booked without a booking date")
    details: list[Detail] = []
    batch_count: int | None = None
    for dtls in _kids(ntry, "NtryDtls"):
        count = _text(dtls, "Btch", "NbOfTxs")
        if count is not None:
            if not _COUNT.fullmatch(count):
                raise CamtError(f"{what}: the number of batch transactions is no number")
            batch_count = (batch_count or 0) + int(count)
        details.extend(_detail(tx, sign, reversal, own_iban, what) for tx in _kids(dtls, "TxDtls"))
    entry_reference = reference(_text(ntry, "AcctSvcrRef"))
    if entry_reference is None and len(details) == 1 and not (batch_count or 0) > 1:
        entry_reference = details[0].reference
    return Entry(
        position=position,
        status=status,
        booking_date=booking_date,
        value_date=_date_of(_kid(ntry, "ValDt"), what),
        amount=value.copy_sign(sign),
        currency=currency,
        reversal=reversal,
        reference=entry_reference,
        bank_transaction_code=_bank_code(_kid(ntry, "BkTxCd")),
        booking_text=_text(ntry, "AddtlNtryInf"),
        details=tuple(details),
        batch_count=batch_count,
    )


def _balances(report: Element, what: str) -> tuple[Decimal | None, date | None, Decimal | None, date | None, str | None]:
    """Opening (``OPBD``, else ``PRCD``) and closing balance (``CLBD``) with their days,
    and the currency they are in."""
    found: dict[str, tuple[Decimal, date | None, str]] = {}
    for balance in _kids(report, "Bal"):
        code = _text(balance, "Tp", "CdOrPrtry", "Cd")
        if code not in ("OPBD", "PRCD", "CLBD"):
            continue
        if code in found:
            raise CamtError(f"{what}: two balances of type {code}")
        value, currency = _amount(_kid(balance, "Amt"), f"{what}, balance {code}")
        found[code] = (value.copy_sign(_sign(balance, f"{what}, balance {code}")), _date_of(_kid(balance, "Dt"), what), currency)
    opening = found["OPBD"] if "OPBD" in found else found.get("PRCD")
    closing = found.get("CLBD")
    currencies = {item[2] for item in (opening, closing) if item is not None}
    if len(currencies) > 1:
        raise CamtError(f"{what}: the balances are in different currencies")
    opening_day = opening[1] if opening else None
    if opening_day is not None and "OPBD" not in found:
        # PRCD is the closing balance of the day before the period.
        if opening_day == date.max:
            raise CamtError(f"{what}: not a date")
        opening_day += timedelta(days=1)
    return (
        opening[0] if opening else None,
        opening_day,
        closing[0] if closing else None,
        closing[1] if closing else None,
        currencies.pop() if currencies else None,
    )


def _period(report: Element, what: str) -> tuple[date | None, date | None, bool]:
    """The period ``FrToDt`` states, and whether it ends with the end of its last day."""
    period = _kid(report, "FrToDt")
    if period is None:
        return None, None, True
    start, end = _text(period, "FrDtTm"), _text(period, "ToDtTm")
    if start is None or end is None:
        raise CamtError(f"{what}: the period (FrToDt) needs FrDtTm and ToDtTm")
    whole_day = True
    if len(end) > 10:
        try:
            moment = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            raise CamtError(f"{what}: not a time") from None
        whole_day = (moment.hour, moment.minute) >= _END_OF_DAY
    return _iso_date(start, what), _iso_date(end, what), whole_day


def _report(kind: CamtKind, element: Element, number: int) -> Report:
    what = f"{'statement' if kind is CamtKind.STATEMENT else 'report'} {number}"
    iban = _text(element, "Acct", "Id", "IBAN")
    if iban is None:
        raise CamtError(f"{what}: the account has no IBAN")
    iban = normalise_iban(iban)
    if not is_valid_iban(iban):
        raise CamtError(f"{what}: invalid account IBAN")
    entries = tuple(_entry(position, ntry, iban) for position, ntry in enumerate(_kids(element, "Ntry"), start=1))
    opening, opening_day, closing, closing_day, balance_currency = _balances(element, what)
    start, end, whole_day = _period(element, what)
    booked_days = [entry.booking_date for entry in entries if entry.booking_date and entry.status is EntryStatus.BOOKED]
    if kind is CamtKind.STATEMENT:
        if opening is None or closing is None:
            raise CamtError(f"{what}: a statement needs its opening (OPBD or PRCD) and closing balance (CLBD)")
        start = start or opening_day
        end = end or closing_day
    start = start or min(booked_days, default=None)
    end = end or max(booked_days, default=None)
    if start is None or end is None:
        raise CamtError(f"{what}: states no period and has no booked entry")
    if end < start:
        raise CamtError(f"{what}: the period ends before it starts")
    complete_until: date | None = end
    if not whole_day:
        complete_until = end - timedelta(days=1) if end > start else None
    currency = _text(element, "Acct", "Ccy") or balance_currency
    sequence = _text(element, "LglSeqNb") or _text(element, "ElctrncSeqNb")
    return Report(
        iban=iban,
        currency=currency.upper() if currency else None,
        period_start=start,
        period_end=end,
        complete_until=complete_until,
        opening_balance=opening,
        closing_balance=closing,
        entries=entries,
        identifier=_text(element, "Id"),
        sequence=sequence,
    )


def read_xml(content: bytes, limits: Limits) -> tuple[CamtKind, str, list[Report]]:
    root = _parse(content, limits)
    match = _NAMESPACE.fullmatch(root.tag)
    if match is None:
        raise CamtError("not a camt.052 or camt.053 document")
    message, version = match.group(1), match.group(2)
    if version not in VERSIONS:
        raise CamtError(f"camt.{message}.001.{version} is not read (versions {', '.join(VERSIONS)})")
    kind = CamtKind.STATEMENT if message == "053" else CamtKind.REPORT
    container, item = ("BkToCstmrStmt", "Stmt") if kind is CamtKind.STATEMENT else ("BkToCstmrAcctRpt", "Rpt")
    elements = _kids(_kid(root, container), item)
    if not elements:
        raise CamtError(f"the {kind} holds no {item}")
    return kind, f"001.{version}", [_report(kind, element, number) for number, element in enumerate(elements, start=1)]
