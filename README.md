# camt-reader

**camt-reader** reads ISO 20022 bank-to-customer statements and reports (camt.053, camt.052), ZIP
containers of them and the CSV-CAMT export of German savings banks into a neutral model:
statements, balances, entries, transaction details, structured and unstructured remittance
information. It is the reading counterpart of
[fints2camt](https://github.com/extdev-gmbh/fints2camt), which fetches statements from a bank and
writes camt.053.

A pure library: bytes in, values out. No network, no file access, no logging, no global state, no
runtime dependencies. Amounts are `Decimal`, dates are `date`, account numbers and identifiers are
strings.

```bash
pip install camt-reader
```

## What it reads

| Format | Versions | Notes |
|---|---|---|
| camt.053 (statement) | `001.02`, `001.08` | needs opening (`OPBD`/`PRCD`) and closing balance (`CLBD`) |
| camt.052 (intraday report) | `001.02`, `001.08` | balances optional; a period ending during the day is not a complete day |
| ZIP of those | | `.xml` members side by side, each a file of its own |
| CSV-CAMT (Sparkasse) | V2, V8 | columns by header name; Windows-1252 or UTF-8; no period, no balances, no bank reference |

The reader is strict: what it does not recognise is an error, never a guess.

## Example

```python
from pathlib import Path

from camt_reader import CamtError, read_upload, remittance_lines

try:
    files = read_upload(Path("statement.zip").read_bytes(), "statement.zip")
except CamtError as error:
    raise SystemExit(f"not readable: {error}")

for file in files:
    for report in file.reports:
        print(file.kind, report.iban, report.period_start, report.period_end)
        print("  opening", report.opening_balance, "closing", report.closing_balance)
        for entry in report.booked:
            print(" ", entry.booking_date, entry.amount, entry.currency)
            print(" ", entry.detail.counterparty_name, entry.detail.counterparty_iban)
            print(" ", " / ".join(remittance_lines(entry)))
```

`read_upload(content, file_name)` takes one file or a ZIP and returns a list of `CamtFile`;
`read_file(content, file_name)` takes exactly one file and is told XML from CSV by the content.

- `CamtFile`: `name`, `sha256`, `kind` (`CamtKind`), `version`, `reports`.
- `Report`: `iban`, `currency`, `period_start`, `period_end`, `complete_until` (the last day whose
  entries are all in the file), `opening_balance`, `closing_balance`, `entries`, `identifier`,
  `sequence`; `booked` and `total` for the booked entries.
- `Entry`: `position`, `status` (`EntryStatus`), `booking_date`, `value_date`, `amount` (signed,
  incoming > 0), `currency`, `reversal`, `reference`, `bank_transaction_code`, `booking_text`,
  `details`, `batch_count`, `batch_reference`; `is_batch`, and `detail` for the one transaction of
  an entry that is no batch.
- `Detail`: `amount`, `counterparty_name`, `counterparty_iban`, `counterparty_bic`, `remittance`,
  `structured_reference`, `end_to_end_id`, `mandate_id`, `creditor_id`, `reference`,
  `bank_transaction_code`, `additional_info`.

`remittance_lines(entry)` is the text of an entry as a German bank transaction carries it, with
`EREF:`/`MREF:`/`CRED:` lines, or a line per transaction for a batch.

## Hostile input

Statements come from outside, so everything in them is treated as hostile:

- **One exception.** Whatever the input, the reader returns values or raises `CamtError` (a
  `ValueError`). Its message names the place, never document content, so it can be logged; a
  ZIP member is named by its position, as bank file names carry the account.
- **Exact amounts.** Reading does no decimal arithmetic, so amounts do not depend on the
  caller's `decimal` context.
- **XML.** A document type declaration is refused the moment the parser meets it, in any encoding;
  so there are no entities and no external references. expat is driven directly rather than
  through `defusedxml`, which refuses the same declarations but has no limit for depth or elements.
- **ZIP.** Stored or deflated members only, no directories, no paths, no duplicate names, no
  encrypted or overlapping members; a member must unpack to exactly the size it announces.
- **Numbers.** Amounts have at most 15 integer digits and are plain digits.

The limits are parameters; the defaults are safe for untrusted input:

```python
from camt_reader import Limits, read_upload

files = read_upload(content, "statement.zip", limits=Limits(max_members=31))
```

| `Limits` field | Default | |
|---|---|---|
| `max_file_size` | 50 MiB | one file: an upload, or one member of a ZIP |
| `max_members` | 400 | files in one ZIP |
| `max_unpacked` | 50 MiB | what all members of one ZIP unpack to |
| `max_ratio` | 200 | unpacked bytes per compressed byte of one member |
| `max_depth` | 32 | nesting of XML elements |
| `max_elements` | 2,000,000 | XML elements in one file |

## Limits of this library

- **No balance completeness check — that is the caller's job.** The reader reports balances and
  entries as the file states them. Whether opening balance plus entries gives the closing balance,
  or whether statements follow each other without a gap, it does not decide.
- **No de-duplication.** An entry has no identity of its own across files.
- **Not validated against the XSD.** It reads what it needs and refuses what it cannot read
  unambiguously, but a file it accepts need not be schema-valid.
- **Not tax advice,** and no certification of any kind (GoBD or other). It is a parser.

## Development

```bash
scripts/test.sh            # the tests, in the CI environment (needs only Docker)
```

Fixtures are invented; the IBANs are the example IBANs of ISO 13616 and the IBAN registry.

Licensed under the [Apache License 2.0](LICENSE). See [CHANGELOG](CHANGELOG.md) and
[SECURITY](SECURITY.md).
