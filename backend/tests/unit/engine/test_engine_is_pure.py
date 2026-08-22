"""The engine performs no I/O, and this is what enforces it.

`app/engine/` promises that every result is a pure function of its `AnalysisContext`.
That promise is worth exactly as much as its enforcement: a single
`from app.providers.weather import ...` added in a hurry would silently make analyses
non-reproducible, untestable without a network, and impossible to recompute from a
stored run.

So the rule is checked mechanically rather than by review. Every module under
`app/engine/` is parsed and its imports compared against an allowlist.

Two safeguards keep this test from going quietly vacuous:

* `test_the_walk_actually_finds_engine_modules` fails if the discovery stops finding
  files — a rename or a moved package would otherwise leave this passing over nothing.
* `test_the_guard_detects_a_forbidden_import` runs the same checker over a synthetic
  module that *does* import a provider, and fails if the checker lets it through.
"""

import ast
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parents[3] / "app" / "engine"

#: Top-level modules the engine may import. Anything else is a failure.
#:
#: `app.schemas` is deliberately permitted: it is pure Pydantic that imports nothing
#: but itself, and the engine emits its types directly. `app.rules` is deliberately
#: NOT permitted — the registry reads files from disk, so the engine receives resolved
#: parameters rather than loading them.
ALLOWED_PREFIXES = frozenset(
    {
        # stdlib
        "abc",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "functools",
        "hashlib",
        "itertools",
        "json",
        "math",
        "statistics",
        "typing",
        # third-party, pure
        "pydantic",
        # first-party, pure
        "app.schemas",
        "app.engine",
    }
)

#: Named explicitly so a failure message can say *why* this one is banned, rather than
#: only that it was not on the allowlist.
FORBIDDEN_REASONS = {
    "app.providers": "network I/O — the engine must receive data, not fetch it",
    "app.db": "database access — breaks purity and reproducibility",
    "app.services": "orchestration layer — would invert the dependency direction",
    "app.core": "settings and request context — the engine reads neither",
    "app.api": "HTTP layer",
    "app.main": "application factory",
    "httpx": "HTTP client",
    "requests": "HTTP client",
    "urllib": "network access",
    "socket": "network access",
    "sqlalchemy": "database access",
    "alembic": "database migrations",
    "random": "non-determinism — the engine must be reproducible",
    "secrets": "non-determinism",
    "os": "environment and filesystem access",
    "pathlib": "filesystem access",
    "time": "wall-clock access — the run date arrives on the context",
    "yaml": "file parsing — rules are resolved before the engine is called",
}


def engine_modules() -> list[Path]:
    return sorted(ENGINE_DIR.rglob("*.py"))


def imported_roots(source: str) -> set[str]:
    """Every module a source file imports, as dotted roots."""
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name)
        # `node.level == 0` excludes relative imports, which cannot leave the package.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module)
    return roots


def violations(source: str) -> list[str]:
    """Imports in `source` that the engine is not permitted to make."""
    found: list[str] = []
    for name in sorted(imported_roots(source)):
        if any(name == p or name.startswith(p + ".") for p in ALLOWED_PREFIXES):
            continue
        reason = next(
            (r for p, r in FORBIDDEN_REASONS.items() if name == p or name.startswith(p + ".")),
            "not on the engine allowlist",
        )
        found.append(f"{name} ({reason})")
    return found


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------


def test_the_walk_actually_finds_engine_modules() -> None:
    """Safeguard. If discovery breaks, every other test here passes over nothing."""
    modules = engine_modules()

    assert len(modules) >= 4, f"only found {len(modules)} engine modules at {ENGINE_DIR}"
    assert {m.name for m in modules} >= {"context.py", "scoring.py", "version.py"}


@pytest.mark.parametrize("module", engine_modules(), ids=lambda p: p.name)
def test_no_engine_module_imports_io(module: Path) -> None:
    offending = violations(module.read_text(encoding="utf-8"))

    assert not offending, f"{module.name} imports " + "; ".join(offending)


def test_the_guard_detects_a_forbidden_import() -> None:
    """Negative control: the checker must reject what it exists to reject."""
    smuggled = "\n".join(
        [
            "import httpx",
            "from app.providers.weather import fetch_daily",
            "from sqlalchemy import select",
            "import random",
        ]
    )

    offending = violations(smuggled)

    assert len(offending) == 4
    assert any("httpx" in v for v in offending)
    assert any("app.providers.weather" in v for v in offending)
    assert any("random" in v and "reproducib" in v for v in offending)


def test_the_guard_permits_what_the_engine_legitimately_needs() -> None:
    """Complement to the control above: it must not reject the allowed set either,
    or the first honest import would be reported as a violation."""
    legitimate = "\n".join(
        [
            "import hashlib",
            "import json",
            "from dataclasses import dataclass",
            "from datetime import date",
            "from app.schemas.common import ScoredFactor",
            "from app.engine.version import ENGINE_VERSION",
        ]
    )

    assert violations(legitimate) == []


def test_the_engine_can_be_imported_without_any_optional_dependency() -> None:
    """Importing the package must not reach for settings, a database URL, or a file.

    A regression here shows up as an ImportError or, worse, a silent read of
    `app.core.config` that makes the engine behave differently per environment.
    """
    import app.engine.context as context
    import app.engine.scoring as scoring
    import app.engine.version as version

    assert version.ENGINE_VERSION
    assert scoring.INSUFFICIENT
    assert context.COORDINATE_PRECISION == 6
