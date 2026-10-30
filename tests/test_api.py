"""The public surface: what the package exports, and what it does not do."""

from __future__ import annotations

import ast
import sys
import tomllib
from datetime import date
from decimal import Decimal
from pathlib import Path

import camt_fixtures as f

import camt_reader
from camt_reader import Limits, read_file

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted((ROOT / "src" / "camt_reader").glob("*.py"))
#: The standard library modules the reader may use: none reaches the network, a file or the clock.
ALLOWED_IMPORTS = {
    "collections", "csv", "dataclasses", "datetime", "decimal", "enum", "hashlib", "io", "re", "struct", "xml",
    "zipfile", "zlib", "__future__",
}  # fmt: skip


def test_the_public_api() -> None:
    assert sorted(camt_reader.__all__) == [
        "CamtError", "CamtFile", "CamtKind", "Detail", "Entry", "EntryStatus", "Limits", "Report",
        "__version__", "read_file", "read_upload", "remittance_lines",
    ]  # fmt: skip
    assert issubclass(camt_reader.CamtError, ValueError)


def test_the_defaults_of_the_limits() -> None:
    assert Limits() == Limits(
        max_file_size=50 * 1024 * 1024,
        max_members=400,
        max_unpacked=50 * 1024 * 1024,
        max_ratio=200,
        max_depth=32,
        max_elements=2_000_000,
    )


def test_the_library_imports_nothing_beyond_its_allowed_modules() -> None:
    imported: set[str] = set()
    for source in SOURCES:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    assert imported <= ALLOWED_IMPORTS
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}


def test_the_library_has_no_runtime_dependency_and_pins_its_tools() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []
    extras = pyproject["project"]["optional-dependencies"]
    assert all("==" in requirement for tools in extras.values() for requirement in tools)
    assert all("==" in requirement for requirement in pyproject["build-system"]["requires"])


def test_the_package_is_typed() -> None:
    assert (ROOT / "src" / "camt_reader" / "py.typed").is_file()


def test_amounts_are_decimals_and_identifiers_strings() -> None:
    day = date(2026, 10, 1)
    [report] = read_file(f.camt("053", "08", day, day, [f.incoming(day, "1.10", "A")], opening="0.20"), "a.xml").reports
    entry = report.entries[0]
    assert report.opening_balance == Decimal("0.20") and isinstance(report.opening_balance, Decimal)
    assert isinstance(entry.amount, Decimal) and isinstance(entry.detail.amount, Decimal)
    assert isinstance(report.iban, str)
