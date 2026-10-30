# Changelog

All notable changes to camt-reader are listed here. The project follows
[Semantic Versioning](https://semver.org/): what the package root exports is the public API,
anything else is private.

## [Unreleased]

## [0.1.0] - 2026-10-30

First release.

### Added

- `read_upload` and `read_file`: camt.053 and camt.052 in the versions 001.02 and 001.08, ZIP
  containers of them, and the CSV-CAMT export of German savings banks (V2 and V8) into a
  neutral model (`CamtFile`, `Report`, `Entry`, `Detail`, `CamtKind`, `EntryStatus`).
- Balances, batches, reversals, structured creditor references, the creditor id of direct
  debits, and `remittance_lines` for the text of an entry.
- `Limits`: file size, ZIP members, unpacked size, compression ratio, XML depth and elements
  as parameters with safe defaults.
- `CamtError` as the only exception for any input; its message never quotes the document.
- Document type declarations refused in every encoding; no runtime dependencies.
- Property and fuzz tests on hostile XML, CSV and ZIP input, and a round trip against the
  camt.053 writer of [fints2camt](https://github.com/extdev-gmbh/fints2camt).
- Releases with a CycloneDX SBOM and build provenance, by trusted publishing to PyPI.
