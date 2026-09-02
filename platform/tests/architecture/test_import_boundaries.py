"""PLAN §3.2: "no service imports another service's internal modules. The only
shared imports permitted are libs/*." Enforced here by literally walking the import
graph rather than trusting the convention to hold as the codebase grows — this is
architecture as an assertion, exactly as the plan called for.
"""
from __future__ import annotations

import ast
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = PLATFORM_ROOT / "services"


def _iter_py_files(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_no_service_imports_another_services_internals() -> None:
    violations: list[str] = []
    service_dirs = [d.name for d in SERVICES_ROOT.iterdir() if d.is_dir() and not d.name.startswith("__")]

    for service_name in service_dirs:
        service_path = SERVICES_ROOT / service_name
        for py_file in _iter_py_files(service_path):
            for module in _imported_modules(py_file):
                if not module.startswith("services."):
                    continue
                imported_service = module.split(".")[1]
                if imported_service != service_name:
                    violations.append(
                        f"{py_file.relative_to(PLATFORM_ROOT)} imports {module!r} "
                        f"(service {imported_service!r}, not its own {service_name!r})"
                    )

    assert not violations, "cross-service import violations found:\n" + "\n".join(violations)


def test_libs_never_imports_from_services() -> None:
    """The dependency direction is one-way: services depend on libs, never the
    reverse. A libs module importing from services would mean the "shared
    contracts library" is quietly coupled to one service's business logic."""
    violations: list[str] = []
    libs_root = PLATFORM_ROOT / "libs"
    for py_file in _iter_py_files(libs_root):
        for module in _imported_modules(py_file):
            if module.startswith("services."):
                violations.append(f"{py_file.relative_to(PLATFORM_ROOT)} imports {module!r}")
    assert not violations, "libs/* must never import from services/*:\n" + "\n".join(violations)
