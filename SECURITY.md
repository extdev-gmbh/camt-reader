# Security policy

camt-reader parses bank statements from untrusted sources. Its handling of hostile input is
described in the [README](README.md#hostile-input).

## Supported versions

Security fixes are made for the latest released minor version. Please update to the
latest release before reporting.

## Reporting a vulnerability

Please do **not** open a public issue for a vulnerability. Report it privately to:

**security@ext.dev**

Include what you found, how to reproduce it, and which version you use. **Never send real
account data** — a synthetic file that shows the problem is enough.

We acknowledge a report within a few working days, keep you informed about the fix, and
credit you in the changelog if you wish.

## Scope

In scope: the code in this repository — for example an input that makes camt-reader raise
anything but `CamtError`, exhaust memory or time beyond its limits, read a file, reach the
network, or put document content into an exception message.

Out of scope: the Python interpreter and its standard library (please report to them
directly), and what an application does with the values it gets.
