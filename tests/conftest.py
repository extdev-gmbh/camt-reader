"""Hypothesis: reproducible, and no example database (the gate mounts the checkout read-only)."""

from hypothesis import settings

settings.register_profile("gate", database=None, derandomize=True, deadline=None, max_examples=200)
settings.load_profile("gate")
