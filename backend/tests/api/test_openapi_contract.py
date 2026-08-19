"""Contract drift guard.

This is the linchpin of the single-branch workflow: it is mechanically impossible
to change the API surface without either regenerating `contracts/openapi.json` or
turning this test red.
"""

import json
from pathlib import Path

import pytest

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi.json"


def test_committed_contract_exists() -> None:
    assert CONTRACT_PATH.exists(), (
        f"{CONTRACT_PATH} is missing. Run `make contract` and commit the result."
    )


def test_openapi_contract_matches_committed(app) -> None:
    """The running app's schema must equal the committed contract, exactly."""
    if not CONTRACT_PATH.exists():
        pytest.skip("contract not generated yet")

    live = json.loads(json.dumps(app.openapi(), sort_keys=True))
    committed = json.loads(CONTRACT_PATH.read_text())

    if live != committed:
        live_paths = set(live.get("paths", {}))
        committed_paths = set(committed.get("paths", {}))
        added = sorted(live_paths - committed_paths)
        removed = sorted(committed_paths - live_paths)
        pytest.fail(
            "contracts/openapi.json is stale.\n"
            f"  paths added since last export:   {added or 'none'}\n"
            f"  paths removed since last export: {removed or 'none'}\n"
            "  Fix: `make contract`, commit as 'chore(contract): regenerate openapi',\n"
            "       then tell the frontend dev to regenerate types.gen.ts."
        )


def test_error_envelope_is_published_in_the_schema(app) -> None:
    """The frontend generates its ApiError type from this — it must be in the schema."""
    schemas = app.openapi()["components"]["schemas"]
    assert "ErrorResponse" in schemas
    assert "ErrorDetail" in schemas

    props = schemas["ErrorDetail"]["properties"]
    assert {"code", "message", "details", "request_id"} <= set(props)


def test_all_routes_are_versioned(app) -> None:
    """Nothing may sit outside /api/v1 — versioning is in the path."""
    unversioned = [
        path
        for path in app.openapi()["paths"]
        if not path.startswith("/api/v1")
    ]
    assert not unversioned, f"unversioned routes found: {unversioned}"
