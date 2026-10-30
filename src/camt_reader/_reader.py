"""Read CAMT files: bytes in, :class:`CamtFile` out.

The reader is strict — what it does not recognise is an error (:class:`CamtError`), never a
guess; whether a statement is complete is the caller's decision, by its balances.
"""

from __future__ import annotations

import hashlib

from ._csv import read_csv
from ._model import CamtError, CamtFile, CamtKind, Limits
from ._xml import read_xml
from ._zip import members

_ZIP_MAGIC = b"PK\x03\x04"
_DEFAULT_LIMITS = Limits()


def _check_size(content: bytes, limits: Limits) -> None:
    if len(content) > limits.max_file_size:
        raise CamtError(f"the file has {len(content)} bytes, at most {limits.max_file_size} are taken")


def read_upload(content: bytes, file_name: str, *, limits: Limits = _DEFAULT_LIMITS) -> list[CamtFile]:
    """The CAMT files in an upload: the file itself, or the members of a ZIP.

    Raises :class:`CamtError` if the upload or any of its files cannot be read.
    """
    if not content:
        raise CamtError("the file is empty")
    _check_size(content, limits)
    if content.startswith(_ZIP_MAGIC):
        return [read_file(data, name, limits=limits) for name, data in members(content, limits)]
    return [read_file(content, file_name, limits=limits)]


def read_file(content: bytes, file_name: str, *, limits: Limits = _DEFAULT_LIMITS) -> CamtFile:
    """One CAMT file: XML (camt.053, camt.052) or CSV-CAMT, told apart by its content.

    Raises :class:`CamtError` if it cannot be read.
    """
    _check_size(content, limits)
    sha256 = hashlib.sha256(content).hexdigest()
    head = content.lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<"):
        kind, version, reports = read_xml(content, limits)
    else:
        kind, version, reports = CamtKind.CSV, *read_csv(content)
    return CamtFile(name=file_name, sha256=sha256, kind=kind, version=version, reports=tuple(reports))
