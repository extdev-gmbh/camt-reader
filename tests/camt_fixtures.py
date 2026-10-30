"""Synthetic CAMT files, built after the published specs (ISO 20022 camt.052/053 in the
versions 001.02 and 001.08 of the DK spec „Anlage 3, Datenformate“, the Sparkasse export
„CSV-CAMT“). No real account data: names, IBANs and references are made up."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

OWNER = "Beispiel GmbH"

# The example IBANs of ISO 13616 and of the national IBAN registry: valid checksums, no one's account.
ACCOUNT_IBAN = "DE89370400440532013000"
OTHER_ACCOUNT_IBAN = "GB82WEST12345698765432"
CUSTOMER_IBAN = "FR1420041010050500013M02606"
SUPPLIER_IBAN = "NL91ABNA0417164300"
INSURER_IBAN = "BE68539007547034"
# The test creditor identifier of the German SEPA documentation.
CREDITOR_ID = "DE98ZZZ09999999999"


@dataclass
class Tx:
    """One entry; ``batch`` makes it a batch booking of those transactions."""

    amount: str
    credit: bool
    booking: date
    value: date | None = None
    ref: str | None = None
    name: str | None = None
    account: str | None = None
    bic: str | None = None
    ustrd: list[str] = field(default_factory=list)
    eref: str | None = None
    mandate: str | None = None
    creditor_id: str | None = None
    status: str = "BOOK"
    reversal: bool = False
    addtl: str | None = None
    code: tuple[str, str, str] = ("PMNT", "RCDT", "ESCT")
    proprietary: str | None = "NTRF+166"
    batch: list[Tx] | None = None


def incoming(day: date, amount: str, text: str, *, ref: str | None = None, **kwargs: object) -> Tx:
    values: dict[str, object] = {
        "name": "Customer AG",
        "account": CUSTOMER_IBAN,
        "bic": "CUSTDEFFXXX",
        "ustrd": [text],
        "eref": f"E2E-{text}",
        "addtl": "SEPA-Gutschrift",
    } | kwargs
    return Tx(amount, True, day, day, ref=ref, **values)  # type: ignore[arg-type]


def transfer(day: date, amount: str, text: str, *, ref: str | None = None, **kwargs: object) -> Tx:
    values: dict[str, object] = {
        "name": "Supplier GmbH",
        "account": SUPPLIER_IBAN,
        "bic": "SUPPDEFFXXX",
        "ustrd": [text],
        "addtl": "SEPA-Überweisung",
        "code": ("PMNT", "ICDT", "ESCT"),
        "proprietary": "NTRF+116",
    } | kwargs
    return Tx(amount, False, day, day, ref=ref, **values)  # type: ignore[arg-type]


def direct_debit(day: date, amount: str, text: str, *, ref: str | None = None, **kwargs: object) -> Tx:
    values: dict[str, object] = {
        "name": "Insurer AG",
        "account": INSURER_IBAN,
        "bic": "INSUDEFFXXX",
        "ustrd": [text],
        "eref": "POLICE-2026-10",
        "mandate": "MANDAT-0815",
        "creditor_id": CREDITOR_ID,
        "addtl": "SEPA-Lastschrift",
        "code": ("PMNT", "IDDT", "ESDD"),
        "proprietary": "NDDT+105",
    } | kwargs
    return Tx(amount, False, day, day, ref=ref, **values)  # type: ignore[arg-type]


def _signed(tx: Tx) -> Decimal:
    return Decimal(tx.amount) if tx.credit else -Decimal(tx.amount)


def total(entries: list[Tx]) -> Decimal:
    return sum((_signed(tx) for tx in entries if tx.status == "BOOK"), Decimal(0))


def _bic(version: str, bic: str | None) -> str:
    if bic is None:
        return ""
    tag = "BICFI" if version == "08" else "BIC"
    return f"<FinInstnId><{tag}>{bic}</{tag}></FinInstnId>"


def _party(version: str, name: str | None, extra: str = "") -> str:
    if name is None and not extra:
        return ""
    inner = f"<Nm>{escape(name)}</Nm>" if name else ""
    return f"<Pty>{inner}{extra}</Pty>" if version == "08" else f"{inner}{extra}"


def _tx_details(version: str, tx: Tx, entry: Tx) -> str:
    refs = "".join(
        f"<{tag}>{escape(value)}</{tag}>"
        for tag, value in (("AcctSvcrRef", None), ("EndToEndId", tx.eref or "NOTPROVIDED"), ("MndtId", tx.mandate))
        if value
    )
    amount = f'<Amt Ccy="EUR">{tx.amount}</Amt>'
    amount_xml = amount if version == "08" else f"<AmtDtls><TxAmt>{amount}</TxAmt></AmtDtls>"
    # The counterparty is the debtor of a credit and the creditor of a debit; we are the other side.
    incoming_ = tx.credit != entry.reversal
    creditor_extra = ""
    if tx.creditor_id:
        creditor_extra = (
            f"<Id><PrvtId><Othr><Id>{tx.creditor_id}</Id><SchmeNm><Prtry>SEPA</Prtry></SchmeNm></Othr></PrvtId></Id>"
        )
    own = f"<Id><IBAN>{ACCOUNT_IBAN}</IBAN></Id>"
    their = f"<Id><IBAN>{tx.account}</IBAN></Id>" if tx.account else ""
    if incoming_:
        parties = (
            f"<Dbtr>{_party(version, tx.name)}</Dbtr><DbtrAcct>{their}</DbtrAcct>"
            f"<Cdtr>{_party(version, OWNER)}</Cdtr><CdtrAcct>{own}</CdtrAcct>"
        )
        agents = f"<DbtrAgt>{_bic(version, tx.bic)}</DbtrAgt>" if tx.bic else ""
    else:
        parties = (
            f"<Dbtr>{_party(version, OWNER)}</Dbtr><DbtrAcct>{own}</DbtrAcct>"
            f"<Cdtr>{_party(version, tx.name, creditor_extra)}</Cdtr><CdtrAcct>{their}</CdtrAcct>"
        )
        agents = f"<CdtrAgt>{_bic(version, tx.bic)}</CdtrAgt>" if tx.bic else ""
    remittance = "".join(f"<Ustrd>{escape(line)}</Ustrd>" for line in tx.ustrd)
    indicator = f"<CdtDbtInd>{'CRDT' if tx.credit else 'DBIT'}</CdtDbtInd>" if entry.batch else ""
    return (
        f"<TxDtls><Refs>{refs}</Refs>{amount_xml}{indicator}<RltdPties>{parties}</RltdPties>"
        f"{f'<RltdAgts>{agents}</RltdAgts>' if agents else ''}"
        f"{f'<RmtInf>{remittance}</RmtInf>' if remittance else ''}</TxDtls>"
    )


def _entry(version: str, tx: Tx) -> str:
    status = f"<Sts><Cd>{tx.status}</Cd></Sts>" if version == "08" else f"<Sts>{tx.status}</Sts>"
    booking = f"<BookgDt><Dt>{tx.booking.isoformat()}</Dt></BookgDt>" if tx.status == "BOOK" else ""
    value = f"<ValDt><Dt>{tx.value.isoformat()}</Dt></ValDt>" if tx.value else ""
    ref = f"<AcctSvcrRef>{tx.ref}</AcctSvcrRef>" if tx.ref else ""
    reversal = "<RvslInd>true</RvslInd>" if tx.reversal else ""
    domain, family, sub = tx.code
    code = f"<Domn><Cd>{domain}</Cd><Fmly><Cd>{family}</Cd><SubFmlyCd>{sub}</SubFmlyCd></Fmly></Domn>"
    if tx.proprietary:
        code += f"<Prtry><Cd>{tx.proprietary}</Cd><Issr>DK</Issr></Prtry>"
    if tx.batch:
        details = (
            f"<NtryDtls><Btch><NbOfTxs>{len(tx.batch)}</NbOfTxs></Btch>"
            + "".join(_tx_details(version, item, tx) for item in tx.batch)
            + "</NtryDtls>"
        )
    else:
        details = f"<NtryDtls>{_tx_details(version, tx, tx)}</NtryDtls>"
    addtl = f"<AddtlNtryInf>{escape(tx.addtl)}</AddtlNtryInf>" if tx.addtl else ""
    return (
        f'<Ntry><Amt Ccy="EUR">{tx.amount}</Amt><CdtDbtInd>{"CRDT" if tx.credit else "DBIT"}</CdtDbtInd>'
        f"{reversal}{status}{booking}{value}{ref}<BkTxCd>{code}</BkTxCd>{details}{addtl}</Ntry>"
    )


def _balance(code: str, amount: Decimal, day: date) -> str:
    indicator = "CRDT" if amount >= 0 else "DBIT"
    return (
        f"<Bal><Tp><CdOrPrtry><Cd>{code}</Cd></CdOrPrtry></Tp>"
        f'<Amt Ccy="EUR">{abs(amount):.2f}</Amt><CdtDbtInd>{indicator}</CdtDbtInd><Dt><Dt>{day.isoformat()}</Dt></Dt></Bal>'
    )


def camt(
    kind: str,
    version: str,
    start: date,
    end: date,
    entries: list[Tx],
    *,
    opening: str | None = None,
    closing: str | None = None,
    account: str = ACCOUNT_IBAN,
    to_time: str = "23:59:59",
    statement_id: str = "STMT-1",
) -> bytes:
    """A camt.053 (``kind`` ``053``: with ``opening`` and the closing balance — computed
    from the entries unless ``closing`` says otherwise) or a camt.052 (``052``)."""
    container, item = ("BkToCstmrStmt", "Stmt") if kind == "053" else ("BkToCstmrAcctRpt", "Rpt")
    balances = ""
    if opening is not None:
        closing_amount = Decimal(closing) if closing is not None else Decimal(opening) + total(entries)
        balances = _balance("PRCD", Decimal(opening), date.fromordinal(start.toordinal() - 1)) + _balance(
            "CLBD", closing_amount, end
        )
    servicer = _bic(version, "TESTDEFFXXX")
    body = (
        f"<{item}><Id>{statement_id}</Id><ElctrncSeqNb>1</ElctrncSeqNb><CreDtTm>{end.isoformat()}T23:59:59</CreDtTm>"
        f"<FrToDt><FrDtTm>{start.isoformat()}T00:00:00</FrDtTm><ToDtTm>{end.isoformat()}T{to_time}</ToDtTm></FrToDt>"
        f"<Acct><Id><IBAN>{account}</IBAN></Id><Ccy>EUR</Ccy><Svcr>{servicer}</Svcr></Acct>"
        f"{balances}{''.join(_entry(version, tx) for tx in entries)}</{item}>"
    )
    namespace = f"urn:iso:std:iso:20022:tech:xsd:camt.{kind}.001.{version}"
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<Document xmlns="{namespace}"><{container}>'
        f"<GrpHdr><MsgId>MSG-{statement_id}</MsgId><CreDtTm>{end.isoformat()}T23:59:59</CreDtTm></GrpHdr>"
        f"{body}</{container}></Document>"
    ).encode()


CSV_HEADER = (
    "Auftragskonto",
    "Buchungstag",
    "Valutadatum",
    "Buchungstext",
    "Verwendungszweck",
    "Glaeubiger ID",
    "Mandatsreferenz",
    "Kundenreferenz (End-to-End)",
    "Sammlerreferenz",
    "Lastschrift Ursprungsbetrag",
    "Auslagenersatz Ruecklastschrift",
    "Beguenstigter/Zahlungspflichtiger",
    "Kontonummer/IBAN",
    "BIC (SWIFT-Code)",
    "Betrag",
    "Waehrung",
    "Info",
)


def _german(amount: Decimal) -> str:
    return f"{amount:.2f}".replace(".", ",")


def csv_camt(entries: list[Tx], *, account: str = ACCOUNT_IBAN, encoding: str = "cp1252") -> bytes:
    """A CSV-CAMT export as the Sparkasse online banking writes it (newest first)."""

    def quoted(values: tuple[str, ...] | list[str]) -> str:
        return ";".join(f'"{value}"' for value in values)

    lines = [quoted(CSV_HEADER)]
    for tx in sorted(entries, key=lambda item: item.booking, reverse=True):
        lines.append(
            quoted(
                [
                    account,
                    f"{tx.booking:%d.%m.%y}",
                    f"{(tx.value or tx.booking):%d.%m.%y}",
                    (tx.addtl or "").upper(),
                    " ".join(tx.ustrd),
                    tx.creditor_id or "",
                    tx.mandate or "",
                    tx.eref or "",
                    "",
                    "",
                    "",
                    tx.name or "",
                    tx.account or "",
                    tx.bic or "",
                    _german(_signed(tx)),
                    "EUR",
                    "Umsatz gebucht" if tx.status == "BOOK" else "Umsatz vorgemerkt",
                ]
            )
        )
    return ("\r\n".join(lines) + "\r\n").encode(encoding)


# A fixed member timestamp: ``writestr(name, ...)`` stamps the current time, so the archive bytes
# (and a pytest id built from them) would differ between two calls across a DOS-time tick.
ZIP_MEMBER_TIME = (1980, 1, 1, 0, 0, 0)


def zipped(*members: tuple[str, bytes]) -> bytes:
    """A deflated ZIP of ``members``; its bytes depend only on the members, never on the clock."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members:
            archive.writestr(zipfile.ZipInfo(name, date_time=ZIP_MEMBER_TIME), content, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()
