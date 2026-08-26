"""Thin CLI wrapper: `python -m tools.migrate`. The actual runner lives in
`libs/persistence/migrate.py` since services need to call it too (the infra proof
test does); this just gives it the same `python -m tools.*` calling convention as
`tools.seed` and `tools.demo`.
"""
from __future__ import annotations

import sys

from libs.persistence.migrate import main

if __name__ == "__main__":
    sys.exit(main())
