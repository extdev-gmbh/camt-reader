"""IBAN normalisation and checksum validation (ISO 13616, mod 97)."""

from __future__ import annotations

import re

_IBAN_RE = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}")


def normalise_iban(value: str) -> str:
    """Upper case without spaces."""
    return re.sub(r"\s+", "", value).upper()


def is_valid_iban(value: str) -> bool:
    """True if ``value`` (normalised) is well-formed and its check digits are correct."""
    iban = normalise_iban(value)
    if not _IBAN_RE.fullmatch(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    return int(digits) % 97 == 1
