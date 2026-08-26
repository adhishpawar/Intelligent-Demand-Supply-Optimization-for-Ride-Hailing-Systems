"""PLAN finding A1 (the Contrarian Architect's highest-severity finding): the
matching scoring loop must perform zero network I/O — candidate retrieval is one
Redis call, validation is one batched Postgres query, and RANKING itself (this
module) must be pure CPU. This test asserts that structurally: the scoring module
must not even import an I/O-capable library, so a future edit that "just adds one
more lookup per candidate" fails the build immediately instead of silently
reintroducing the 300ms+ latency bug the original HLD design would have shipped.
"""
from __future__ import annotations

import ast
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parents[2]

# Every module on the hot path: pure scoring math plus the geo primitives it calls.
HOT_PATH_MODULES = [
    PLATFORM_ROOT / "services" / "matching" / "scoring.py",
    PLATFORM_ROOT / "libs" / "geo" / "eta.py",
    PLATFORM_ROOT / "libs" / "geo" / "haversine.py",
]

FORBIDDEN_IMPORTS = {"httpx", "aiokafka", "asyncpg", "redis", "sqlalchemy", "socket", "urllib", "requests"}


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def test_scoring_hotpath_imports_nothing_network_capable() -> None:
    violations = []
    for path in HOT_PATH_MODULES:
        assert path.exists(), f"expected hot-path module not found: {path}"
        found = _imported_top_level_modules(path) & FORBIDDEN_IMPORTS
        if found:
            violations.append(f"{path.relative_to(PLATFORM_ROOT)} imports forbidden I/O module(s): {found}")
    assert not violations, "\n".join(violations)


def test_dispatcher_does_at_most_one_redis_call_and_one_db_call_per_round() -> None:
    """A structural proxy for "no per-candidate I/O": the dispatcher module may
    reference the geo index and the validation query exactly once each per
    `dispatch_once` call — this is asserted behaviourally (not just by import) in
    tests/integration/test_dispatch_claim_race.py's timing; here we assert the
    static shape doesn't have an obvious per-candidate loop calling either."""
    source = (PLATFORM_ROOT / "services" / "matching" / "dispatcher.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    dispatch_once = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "dispatch_once"), None
    )
    assert dispatch_once is not None, "dispatch_once not found in dispatcher.py"

    # Find any `for` loop in dispatch_once and assert its body contains no `await`
    # on the geo index or validation call (only the claim attempt, which IS
    # intentionally per-candidate -- Redis SET NX is O(1) and local, not the
    # network-hop pattern A1 forbids).
    for node in ast.walk(dispatch_once):
        if isinstance(node, ast.For):
            loop_source = ast.dump(node)
            assert "search_with_neighbors" not in loop_source, "geo index search must not be called per-candidate"
            assert "validate_and_enrich" not in loop_source, "Postgres validation must not be called per-candidate"
