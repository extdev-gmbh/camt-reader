"""Hostile input: the reader answers everything with a value or a CamtError, within its limits."""

from __future__ import annotations

import io
import re
import struct
import traceback
import zipfile
from collections.abc import Callable
from datetime import date

import camt_fixtures as f
import pytest
from hypothesis import given
from hypothesis import strategies as st

from camt_reader import CamtError, Limits, read_file, read_upload

OCT_1 = date(2026, 10, 1)
DEFAULT = Limits()
STATEMENT = f.camt("053", "08", OCT_1, OCT_1, [f.incoming(OCT_1, "1.00", "A"), f.direct_debit(OCT_1, "2.00", "B")], opening="9.00")
CSV = f.csv_camt([f.incoming(OCT_1, "1.00", "A")])


def _refused(content: bytes, *, limits: Limits = DEFAULT) -> str:
    with pytest.raises(CamtError) as caught:
        read_upload(content, "file", limits=limits)
    return str(caught.value)


def _logged(content: bytes) -> str:
    """All a log shows of the refusal: the message and the traceback with its causes."""
    with pytest.raises(CamtError) as caught:
        read_upload(content, "file")
    return "".join(traceback.format_exception(caught.value))


def _reads_or_refuses(content: bytes) -> None:
    try:
        read_upload(content, "file")
    except CamtError:
        pass


def _with_csv_cell(column: str, value: str) -> bytes:
    """The CSV fixture with one cell of its data row replaced."""
    at = f.CSV_HEADER.index(column)
    head, row, *_ = CSV.decode("cp1252").split("\r\n")
    cells = row.split(";")
    cells[at] = f'"{value}"'
    return f"{head}\r\n{';'.join(cells)}\r\n".encode()


# --- XML ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        pytest.param('<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>', id="internal"),
        pytest.param('<!DOCTYPE x SYSTEM "file:///etc/passwd"><x/>', id="external"),
        pytest.param("<!DOCTYPE x><x/>", id="no-subset"),
    ],
)
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-16-be", "utf-16"])
def test_a_document_type_is_refused_in_every_encoding(document: str, encoding: str) -> None:
    _refused(document.encode(encoding))


@pytest.mark.parametrize("declared", ["utf-8", "iso-8859-1"])
def test_a_document_type_is_refused_after_an_encoding_declaration(declared: str) -> None:
    content = f'<?xml version="1.0" encoding="{declared}"?><!DOCTYPE x [<!ENTITY a "b">]><x/>'.encode(declared)
    assert "document type" in _refused(content)


def test_the_billion_laughs_is_refused_before_any_entity_is_expanded() -> None:
    entities = "".join(f'<!ENTITY l{n} "{f"&l{n - 1};" * 10}">' for n in range(1, 10))
    content = f'<!DOCTYPE x [<!ENTITY l0 "ha">{entities}]><x>&l9;</x>'.encode()
    assert "document type" in _refused(content)


def test_an_entity_without_declaration_is_refused() -> None:
    assert "cannot be read" in _refused(b"<x>&undeclared;</x>")


def test_the_word_doctype_in_a_text_is_harmless() -> None:
    content = STATEMENT.replace(b"<Ustrd>A</Ustrd>", b"<Ustrd><![CDATA[<!DOCTYPE x>]]></Ustrd>")
    [file] = read_upload(content, "a.xml")
    assert file.reports[0].entries[0].detail.remittance == ("<!DOCTYPE x>",)


def test_deep_nesting_is_refused() -> None:
    assert "nests deeper" in _refused(b"<a>" * 100_000 + b"</a>" * 100_000)


def test_the_element_limit_is_a_parameter() -> None:
    assert "more than 10 elements" in _refused(STATEMENT, limits=Limits(max_elements=10))
    assert "nests deeper than 2" in _refused(STATEMENT, limits=Limits(max_depth=2))


@pytest.mark.parametrize(
    "amount", ["1" * 16, "1" * 100_000, "1.123456", "1e5", "-1.00", "٣.٠٠", "Infinity", "NaN"]
)
def test_what_is_no_plain_amount_is_refused(amount: str) -> None:
    content = STATEMENT.replace(b">1.00</Amt>", f">{amount}</Amt>".encode())
    assert "not an amount" in _refused(content)


@pytest.mark.parametrize("count", ["9" * 5000, "²", "-1", "1.5"])
def test_what_is_no_batch_count_is_refused(count: str) -> None:
    batch = f.Tx("3.00", False, OCT_1, OCT_1, batch=[f.transfer(OCT_1, "1.00", "A"), f.transfer(OCT_1, "2.00", "B")])
    content = f.camt("053", "08", OCT_1, OCT_1, [batch], opening="9.00").replace(
        b"<NbOfTxs>2<", f"<NbOfTxs>{count}<".encode()
    )
    assert "number of batch transactions" in _refused(content)


@pytest.mark.parametrize("day", ["2026-W40-1", "20261001", "2026-13-01", "9999-99-99"])
def test_what_is_no_day_is_refused(day: str) -> None:
    assert "not a date" in _refused(STATEMENT.replace(b"<BookgDt><Dt>2026-10-01<", f"<BookgDt><Dt>{day}<".encode()))


def test_the_days_at_the_end_of_the_calendar_are_refused_or_read() -> None:
    last = b"<Dt><Dt>9999-12-31</Dt></Dt>"
    prcd = re.sub(rb"(<Cd>PRCD</Cd>.*?)<Dt><Dt>[0-9-]+</Dt></Dt>", lambda m: m.group(1) + last, STATEMENT, count=1)
    assert prcd != STATEMENT
    _refused(prcd.replace(b"<FrToDt>", b"<x>").replace(b"</FrToDt>", b"</x>"))


def test_an_intraday_report_on_the_first_day_of_the_calendar_has_no_whole_day() -> None:
    content = f.camt("052", "08", date.min, date.min, [], to_time="10:00:00")
    assert read_file(content, "a.xml").reports[0].complete_until is None


@pytest.mark.parametrize("encoding", ["nonsense", "rot13", "base64", "idna", "undefined", "utf-7"])
def test_an_unusable_encoding_declaration_is_refused(encoding: str) -> None:
    _reads_or_refuses(f'<?xml version="1.0" encoding="{encoding}"?><x/>'.encode())


def test_errors_never_quote_the_document() -> None:
    secret = "GEHEIM"
    for content in (
        STATEMENT.replace(b">1.00</Amt>", f">{secret}</Amt>".encode()),
        STATEMENT.replace(b"<Dt>2026-10-01<", f"<Dt>{secret}<".encode()),
        STATEMENT.replace(f.ACCOUNT_IBAN.encode(), secret.encode() + b"000000000000"),
        STATEMENT.replace(b"<Cd>BOOK</Cd>", f"<Cd>{secret}</Cd>".encode()),
        _with_csv_cell("Betrag", secret),
        _with_csv_cell("Buchungstag", secret),
        _with_csv_cell("Waehrung", secret),
        _with_csv_cell("Auftragskonto", secret),
        f.camt("052", "08", OCT_1, OCT_1, [], to_time=secret),
        f'<?xml version="1.0" encoding="{secret}"?><x/>'.encode(),
    ):
        assert secret not in _logged(content)


BANK_FILE = "2026-10-01_C53_GEHEIM_EUR.xml"


def _encrypted(content: bytes) -> bytes:
    damaged = bytearray(content)
    damaged[6] |= 1
    damaged[damaged.rindex(b"PK\x01\x02") + 8] |= 1
    return bytes(damaged)


def _other_method(name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_BZIP2) as archive:
        archive.writestr(name, STATEMENT)
    return buffer.getvalue()


def _corrupt(content: bytes) -> bytes:
    damaged = bytearray(content)
    damaged[len(BANK_FILE) + 40 : len(BANK_FILE) + 60] = b"\xff" * 20
    return bytes(damaged)


def _other_name_in_the_local_header(content: bytes) -> bytes:
    return content.replace(b"GEHEIM", b"GEHEIX", 1)


def _other_size(content: bytes) -> bytes:
    at = content.rindex(b"PK\x01\x02")
    return content[: at + 24] + struct.pack("<I", len(STATEMENT) + 100) + content[at + 28 :]


def _wrong_checksum(content: bytes) -> bytes:
    at = content.rindex(b"PK\x01\x02")
    return content[: at + 16] + b"\0\0\0\0" + content[at + 20 :]


@pytest.mark.parametrize(
    ("build", "refusal"),
    [
        pytest.param(lambda: f.zipped((f"{BANK_FILE}/a.xml", STATEMENT)), "has a path", id="path"),
        pytest.param(lambda: f.zipped((f"{BANK_FILE}.txt", STATEMENT)), "no XML file", id="no-xml"),
        pytest.param(lambda: f.zipped((f"\x1b{BANK_FILE}", STATEMENT)), "unprintable", id="unprintable"),
        pytest.param(lambda: _encrypted(f.zipped((BANK_FILE, STATEMENT))), "encrypted", id="encrypted"),
        pytest.param(lambda: _other_method(BANK_FILE), "neither stored", id="method"),
        pytest.param(lambda: f.zipped((BANK_FILE, b"0" * (1024 * 1024))), "compressed more", id="ratio"),
        pytest.param(lambda: _corrupt(f.zipped((BANK_FILE, STATEMENT))), "cannot be read", id="corrupt"),
        pytest.param(lambda: _wrong_checksum(f.zipped((BANK_FILE, STATEMENT))), "cannot be read", id="checksum"),
        pytest.param(
            lambda: _other_name_in_the_local_header(f.zipped((BANK_FILE, STATEMENT))), "cannot be read", id="header"
        ),
        pytest.param(lambda: _other_size(f.zipped((BANK_FILE, STATEMENT))), "ZIP member 1", id="size"),
    ],
)
def test_a_refused_member_is_named_by_its_position_only(build: Callable[[], bytes], refusal: str) -> None:
    content = build()
    assert refusal in _refused(content) and "ZIP member 1" in _refused(content)
    assert "GEHEIM" not in _logged(content)


def test_the_position_of_a_member_is_its_place_in_the_archive() -> None:
    assert "ZIP member 2 " in _refused(f.zipped(("a.xml", STATEMENT), ("b.txt", b"x")))
    damaged = _other_name_in_the_local_header(f.zipped((BANK_FILE, STATEMENT), ("0.xml", STATEMENT)))
    assert "ZIP member 1 cannot be read" in _refused(damaged)


@given(st.binary(max_size=2000))
def test_any_bytes_are_read_or_refused(content: bytes) -> None:
    _reads_or_refuses(content)


@given(st.binary(max_size=200))
def test_any_bytes_after_an_xml_start_are_read_or_refused(content: bytes) -> None:
    _reads_or_refuses(b'<?xml version="1.0"?>' + content)
    _reads_or_refuses(b"<Document>" + content)


@given(st.integers(0, len(STATEMENT) - 1), st.binary(min_size=1, max_size=40))
def test_a_damaged_statement_is_read_or_refused(at: int, patch: bytes) -> None:
    _reads_or_refuses(STATEMENT[:at] + patch + STATEMENT[at + len(patch) :])


@given(st.sampled_from(sorted(set(re.findall(rb">([^<>]+)</", STATEMENT)))), st.text(max_size=60))
def test_any_text_in_any_element_is_read_or_refused(value: bytes, text: str) -> None:
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").encode("utf-8", "surrogatepass")
    _reads_or_refuses(STATEMENT.replace(b">" + value + b"</", b">" + escaped + b"</"))


@given(st.lists(st.sampled_from(["Ntry", "NtryDtls", "TxDtls", "Bal", "Amt", "Dt", "Sts"]), max_size=20))
def test_any_nesting_of_camt_elements_is_read_or_refused(tags: list[str]) -> None:
    inner = "".join(f"<{tag}>" for tag in tags) + "".join(f"</{tag}>" for tag in reversed(tags))
    _reads_or_refuses(STATEMENT.replace(b"<Ntry>", inner.encode() + b"<Ntry>", 1))


# --- CSV ------------------------------------------------------------------------------------


@pytest.mark.parametrize("amount", ["1" * 16, "1" * 100_000, "1.000.000.000.000.000.000,00", "1e5", "NaN", "٣,٠٠"])
def test_a_huge_or_strange_csv_amount_is_refused(amount: str) -> None:
    assert "not an amount" in _refused(_with_csv_cell("Betrag", amount))


def test_a_cell_beyond_the_csv_field_limit_is_refused() -> None:
    assert "cannot be read" in _refused(_with_csv_cell("Verwendungszweck", "x" * 200_000))


def test_a_header_of_too_many_columns_is_refused() -> None:
    assert "at most 200 are taken" in _refused(b"a;" * 1_000_000)


def test_the_first_day_of_the_calendar_in_a_csv_is_harmless() -> None:
    [report] = read_file(_with_csv_cell("Buchungstag", "01.01.0001"), "a.csv").reports
    assert report.complete_until is None


def test_invalid_bytes_are_refused() -> None:
    assert "neither XML nor a text file" in _refused(b"\xef\xbb\xbf\xff\xfe")
    assert "neither XML nor a text file" in _refused(b"\x81\x8d\x8f\x90\x9d")


@given(st.text(alphabet=";\"\r\n ,.-+0123456789aZ", max_size=200))
def test_any_csv_body_is_read_or_refused(body: str) -> None:
    head = CSV.split(b"\r\n", 1)[0]
    _reads_or_refuses(head + b"\r\n" + body.encode())


@given(st.sampled_from(f.CSV_HEADER), st.text(max_size=40).filter(lambda text: '"' not in text))
def test_any_text_in_any_csv_cell_is_read_or_refused(column: str, value: str) -> None:
    _reads_or_refuses(_with_csv_cell(column, value.encode("utf-8", "replace").decode("utf-8", "replace")))


@given(st.sampled_from(["utf-16", "utf-32", "latin-1", "shift_jis", "cp037"]))
def test_a_csv_in_a_wrong_encoding_is_read_or_refused(encoding: str) -> None:
    _reads_or_refuses(CSV.decode("cp1252").encode(encoding, "replace"))


# --- ZIP ------------------------------------------------------------------------------------


def test_a_zip_bomb_is_refused_by_its_directory() -> None:
    # 100 MB of zeros, deflated.
    bomb = f.zipped(("bomb.xml", b"0" * (100 * 1024 * 1024)))
    assert len(bomb) < 200_000
    assert "unpacks to more than 50 MB" in _refused(bomb)


def test_a_compression_ratio_beyond_the_limit_is_refused() -> None:
    assert "compressed more than 200:1" in _refused(f.zipped(("bomb.xml", b"0" * (1024 * 1024))))


def test_the_ratio_limit_is_a_parameter() -> None:
    content = f.zipped(("a.xml", STATEMENT))
    assert read_upload(content, "a.zip")
    assert "compressed more than 1:1" in _refused(content, limits=Limits(max_ratio=1))


def test_a_long_statement_stays_within_the_default_ratio() -> None:
    entries = [f.incoming(OCT_1, f"{n}.00", f"RE{n:06d}", ref=f"REF{n:012d}") for n in range(1, 3000)]
    content = f.zipped(("a.xml", f.camt("053", "08", OCT_1, OCT_1, entries, opening="0.00")))
    assert len(read_upload(content, "a.zip")[0].reports[0].entries) == 2999


def _with_declared_size(content: bytes, size: int) -> bytes:
    """``content`` (a ZIP of one member) with another size in its central directory."""
    at = content.rindex(b"PK\x01\x02")
    return content[: at + 24] + struct.pack("<I", size) + content[at + 28 :]


def test_a_member_of_another_size_than_it_announces_is_refused() -> None:
    content = f.zipped(("a.xml", STATEMENT))
    assert "cannot be read" in _refused(_with_declared_size(content, 10))
    assert re.search("cannot be read|not the size", _refused(_with_declared_size(content, len(STATEMENT) + 100)))


def test_too_many_members_are_refused() -> None:
    assert "at most 2 are taken" in _refused(
        f.zipped(*((f"{n}.xml", STATEMENT) for n in range(3))), limits=Limits(max_members=2)
    )
    assert "at most 400 are taken" in _refused(f.zipped(*((f"{n}.xml", b"") for n in range(401))))


def test_the_unpacked_limit_is_a_parameter() -> None:
    assert "unpacks to more than 0 MB" in _refused(f.zipped(("a.xml", STATEMENT)), limits=Limits(max_unpacked=100))


def test_the_file_size_limit_is_a_parameter() -> None:
    assert "at most 100 are taken" in _refused(STATEMENT, limits=Limits(max_file_size=100))
    with pytest.raises(CamtError, match="at most 100 are taken"):
        read_file(STATEMENT, "a.xml", limits=Limits(max_file_size=100))
    # A ZIP member counts as a file of its own, however small the ZIP.
    limit = len(STATEMENT) - 1
    assert f"at most {limit} are taken" in _refused(f.zipped(("a.xml", STATEMENT)), limits=Limits(max_file_size=limit))


@pytest.mark.parametrize("name", ["dir/a.xml", "../a.xml", "/etc/a.xml", "dir\\a.xml", "C:\\a.xml"])
def test_paths_in_a_zip_are_refused(name: str) -> None:
    assert "only files side by side" in _refused(f.zipped((name, STATEMENT)))


def test_a_directory_in_a_zip_is_refused() -> None:
    assert "only files side by side" in _refused(f.zipped(("dir/", b""), ("a.xml", STATEMENT)))


def test_a_nested_zip_is_refused() -> None:
    assert "no XML file" in _refused(f.zipped(("inner.zip", f.zipped(("a.xml", STATEMENT)))))
    with pytest.raises(CamtError):
        read_upload(f.zipped(("inner.xml", f.zipped(("a.xml", STATEMENT)))), "a.zip")


def test_a_name_twice_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive, pytest.warns(UserWarning):
        archive.writestr("a.xml", STATEMENT)
        archive.writestr("a.xml", STATEMENT)
    assert "file name twice" in _refused(buffer.getvalue())


def test_a_control_character_in_a_name_is_refused() -> None:
    assert "unprintable file name" in _refused(f.zipped(("a\x1b[2J.xml", STATEMENT)))


def test_overlapping_members_are_refused() -> None:
    # Two directory entries of different names for the bytes of one member.
    content = f.zipped(("a.xml", STATEMENT))
    start = content.index(b"PK\x01\x02")
    end = content.index(b"PK\x05\x06")
    entry = content[start:end]
    twin = entry.replace(b"a.xml", b"b.xml")
    record = bytearray(content[end:])
    struct.pack_into("<HH", record, 8, 2, 2)
    struct.pack_into("<I", record, 12, 2 * len(entry))
    assert "larger than the ZIP" in _refused(content[:start] + entry + twin + bytes(record))


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_other_compression_methods_are_refused(method: int) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as archive:
        archive.writestr("a.xml", STATEMENT)
    assert "neither stored nor deflated" in _refused(buffer.getvalue())


def test_an_encrypted_member_is_refused() -> None:
    content = bytearray(f.zipped(("a.xml", STATEMENT)))
    content[6] |= 1
    content[content.rindex(b"PK\x01\x02") + 8] |= 1
    assert "encrypted" in _refused(bytes(content))


def test_a_corrupt_deflate_stream_is_refused() -> None:
    content = bytearray(f.zipped(("a.xml", STATEMENT)))
    content[40:60] = b"\xff" * 20
    assert "cannot be read" in _refused(bytes(content))


def test_an_empty_or_cut_zip_is_refused() -> None:
    assert "no file" in _refused(_empty_zip_with_magic())
    assert "cannot be read" in _refused(f.zipped(("a.xml", STATEMENT))[:100])


def _empty_zip_with_magic() -> bytes:
    """A ZIP without members that still starts like one with members."""
    buffer = io.BytesIO(b"PK\x03\x04")
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.comment = b""
    return buffer.getvalue()


@given(st.binary(max_size=500))
def test_any_bytes_behind_the_zip_magic_are_read_or_refused(content: bytes) -> None:
    _reads_or_refuses(b"PK\x03\x04" + content)


ARCHIVE = f.zipped(("a.xml", STATEMENT), ("b.xml", STATEMENT.replace(b"STMT-1", b"STMT-2")))


@given(st.integers(0, len(ARCHIVE) - 1), st.binary(min_size=1, max_size=8))
def test_a_damaged_zip_is_read_or_refused(at: int, patch: bytes) -> None:
    _reads_or_refuses(ARCHIVE[:at] + patch + ARCHIVE[at + len(patch) :])
