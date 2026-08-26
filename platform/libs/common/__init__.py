"""Shared, dependency-free primitives used by every service: config, ids, clock, errors, logging.

Nothing in this package is allowed to import from `libs.persistence`, `libs.eventbus`,
`libs.security`, `libs.geo`, or `libs.contracts` — this is the bottom of the dependency
graph and `tests/architecture/test_import_boundaries.py` asserts it stays that way.
"""
