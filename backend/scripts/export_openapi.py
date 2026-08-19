"""Export the live OpenAPI schema to contracts/openapi.json.

This file is the machine-readable contract between the two developers. It is
committed to git; the frontend generates its TypeScript types from it and never
reads backend source.

Usage:  uv run python scripts/export_openapi.py
        make contract
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402

OUTPUT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"


def main() -> int:
    schema = app.openapi()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    previous = OUTPUT.read_text() if OUTPUT.exists() else None
    # sort_keys keeps the diff stable so a regeneration with no API change
    # produces no git noise.
    rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(rendered)

    paths = len(schema.get("paths", {}))
    schemas = len(schema.get("components", {}).get("schemas", {}))
    changed = previous != rendered

    print(f"{'updated' if changed else 'unchanged'}: {OUTPUT}")
    print(f"  {paths} paths, {schemas} schemas")
    if changed and previous is not None:
        print("\n  >>> Contract changed. Commit as 'chore(contract): regenerate openapi'")
        print("      and tell the frontend dev to regenerate types.gen.ts.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
