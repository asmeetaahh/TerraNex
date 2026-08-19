"""Loads the crop reference catalog into the in-memory store.

Crop ids are derived with `uuid5` from the crop code rather than generated randomly.
That makes them stable across restarts, processes and machines — so a `crop_id` a
frontend hardcodes in a fixture keeps working, and it will keep working when the
catalog moves into Postgres with the same derivation.
"""

import json
from functools import lru_cache
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.logging import get_logger
from app.db.memory import InMemoryStore, store
from app.schemas.crop import Crop

logger = get_logger(__name__)

FIXTURES = Path(__file__).parent / "fixtures"
CROPS_FIXTURE = FIXTURES / "crops.json"

# Namespace for deterministic crop ids. Changing this changes every crop id.
CROP_NAMESPACE = uuid5(NAMESPACE_URL, "https://terranex.app/crops")


def crop_id_for(code: str) -> UUID:
    """The stable id for a crop code."""
    return uuid5(CROP_NAMESPACE, code)


@lru_cache(maxsize=1)
def load_crop_catalog() -> list[Crop]:
    """Parse and validate the catalog fixture. Cached — the file never changes at runtime."""
    raw = json.loads(CROPS_FIXTURE.read_text(encoding="utf-8"))
    return [Crop(id=crop_id_for(entry["code"]), **entry) for entry in raw]


def seed_crops(target: InMemoryStore | None = None) -> int:
    """Populate the crop catalog. Idempotent."""
    target = target if target is not None else store
    catalog = load_crop_catalog()

    with target.lock:
        target.crops.clear()
        target.crops_by_code.clear()
        for crop in catalog:
            target.crops[crop.id] = crop
            target.crops_by_code[crop.code] = crop

    return len(catalog)
