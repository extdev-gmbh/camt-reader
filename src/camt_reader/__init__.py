"""camt-reader: read ISO 20022 camt.052/camt.053 bank statements (also ZIP and CSV-CAMT) into a neutral model.

The public API is what this module exports; everything else is private.
"""

from ._model import CamtError, CamtFile, CamtKind, Detail, Entry, EntryStatus, Limits, Report, remittance_lines
from ._reader import read_file, read_upload

__version__ = "0.1.0"

__all__ = [
    "CamtError",
    "CamtFile",
    "CamtKind",
    "Detail",
    "Entry",
    "EntryStatus",
    "Limits",
    "Report",
    "__version__",
    "read_file",
    "read_upload",
    "remittance_lines",
]
