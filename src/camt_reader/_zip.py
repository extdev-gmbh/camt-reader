"""ZIP containers: several XML files in one archive, as banks deliver the daily statements of a period.

Each member is a file of its own. Everything the archive's directory says is hostile until
checked: only ``.xml`` members side by side (no directories, no paths), stored or deflated,
not encrypted, within the limits for members, unpacked bytes and compression ratio — and a
member that unpacks to anything else than the size it announces is refused.

Errors name a member by its place in the archive, never by its name: bank file names carry the
account, and the name of a hostile file can say anything.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib

from ._model import CamtError, Limits

#: What :mod:`zipfile` raises for a broken archive, besides ``BadZipFile``.
_BROKEN = (
    zipfile.BadZipFile, zlib.error, struct.error, ValueError, IndexError, OverflowError,
    NotImplementedError, RuntimeError, OSError, EOFError,
)  # fmt: skip
_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
_MIB = 1024 * 1024


def _check(infos: list[zipfile.ZipInfo], size: int, limits: Limits) -> None:
    """Refuse an archive by its directory, before anything is unpacked."""
    if not infos:
        raise CamtError("the ZIP holds no file")
    if len(infos) > limits.max_members:
        raise CamtError(f"the ZIP holds {len(infos)} files, at most {limits.max_members} are taken")
    if sum(info.file_size for info in infos) > limits.max_unpacked:
        raise CamtError(f"the ZIP unpacks to more than {limits.max_unpacked // _MIB} MB")
    # Compressed members sharing their bytes (overlapping entries) cannot fit the archive.
    if sum(info.compress_size for info in infos) > size:
        raise CamtError("the members of the ZIP are larger than the ZIP")
    if len({info.filename for info in infos}) != len(infos):
        raise CamtError("the ZIP holds a file name twice")
    for number, info in enumerate(infos, start=1):
        member = f"ZIP member {number}"
        if "/" in info.filename or "\\" in info.filename:
            raise CamtError(f"{member} has a path; only files side by side are taken")
        if not info.filename.isprintable():
            raise CamtError(f"{member} has an unprintable file name")
        if not info.filename.lower().endswith(".xml"):
            raise CamtError(f"{member} is no XML file")
        if info.flag_bits & 0x1:
            raise CamtError(f"{member} is encrypted")
        if info.compress_type not in _METHODS:
            raise CamtError(f"{member} is neither stored nor deflated")
        if info.file_size > limits.max_ratio * max(info.compress_size, 1):
            raise CamtError(f"{member} is compressed more than {limits.max_ratio}:1")


def _read(archive: zipfile.ZipFile, info: zipfile.ZipInfo, number: int) -> bytes:
    try:
        # Read at most what the directory announces: a member that unpacks to more is refused.
        with archive.open(info) as member:
            data = member.read(info.file_size + 1)
    except _BROKEN:
        # Not chained: zipfile quotes the member's name, and a logged traceback shows the cause.
        raise CamtError(f"ZIP member {number} cannot be read") from None
    if len(data) != info.file_size:
        raise CamtError(f"ZIP member {number} is not the size the ZIP states")
    return data


def members(content: bytes, limits: Limits) -> list[tuple[str, bytes]]:
    """The name and content of each member, by name."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except _BROKEN as exc:
        raise CamtError("the ZIP cannot be read") from exc
    with archive:
        infos = archive.infolist()
        _check(infos, len(content), limits)
        numbered = sorted(enumerate(infos, start=1), key=lambda item: item[1].filename)
        return [(info.filename, _read(archive, info, number)) for number, info in numbered]
