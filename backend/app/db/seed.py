"""Loads the crop reference catalog, and optionally demo farms, into the store.

Crop ids are derived with `uuid5` from the crop code rather than generated randomly.
That makes them stable across restarts, processes and machines — so a `crop_id` a
frontend hardcodes in a fixture keeps working, and it will keep working when the
catalog moves into Postgres with the same derivation.
"""

import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.logging import get_logger
from app.db.memory import FarmCropRecord, FarmRecord, InMemoryStore, store
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


# --------------------------------------------------------------------------
# Demo farms
#
# Seeded only when SEED_DEMO_DATA is true, so a fresh instance demonstrates the
# product instead of showing an empty farm list. Without this, `GET /farms`
# correctly returns `[]` on a new process and every downstream panel has nothing
# to render.
#
# Every coordinate below is a real agricultural location taken from the offline
# gazetteer in `app.providers.geocoding` — nothing here is generated. That matters
# because these coordinates drive real provider lookups when WEATHER_PROVIDER is
# set to open_meteo; a fabricated point would produce confidently wrong weather.
# --------------------------------------------------------------------------

DEMO_NAMESPACE = uuid5(NAMESPACE_URL, "https://terranex.app/demo")


def demo_id(kind: str, slug: str) -> UUID:
    """A stable id for a demo record.

    Derived rather than generated, so a demo farm keeps the same id across
    restarts and across machines — which is what makes seeding idempotent and
    lets a frontend fixture reference a demo farm by id.
    """
    return uuid5(DEMO_NAMESPACE, f"{kind}:{slug}")


# Planting dates are expressed relative to today so the demo always shows crops
# mid-season rather than drifting into the past. Given a date, the result is fully
# determined — the same property the weather simulator has.
DEMO_FARMS: list[dict[str, Any]] = [
    {
        "slug": "nashik-vineyard-block",
        "name": "Nashik Block A",
        # Nashik, Maharashtra — India's grape and onion belt.
        "latitude": 19.9975,
        "longitude": 73.7898,
        "country_code": "IN",
        "region": "Maharashtra",
        "elevation_m": 584.0,
        "area_hectares": 8.4,
        "irrigation_type": "drip",
        "farming_practice": "conventional",
        "notes": "Demo farm. Drip-irrigated block on the Deccan plateau.",
        "crops": [
            {
                "crop_code": "onion",
                "growth_stage": "vegetative",
                "status": "growing",
                "is_primary": True,
                "planted_days_ago": 55,
                "season_days": 130,
                "area_hectares": 5.0,
            },
            {
                "crop_code": "tomato",
                "growth_stage": "flowering",
                "status": "growing",
                "is_primary": False,
                "planted_days_ago": 40,
                "season_days": 110,
                "area_hectares": 3.4,
            },
        ],
    },
    {
        "slug": "nakuru-maize-field",
        "name": "Nakuru Highland Field",
        # Nakuru, Kenya — Rift Valley maize country.
        "latitude": -0.3031,
        "longitude": 36.0800,
        "country_code": "KE",
        "region": "Nakuru County",
        "elevation_m": 1850.0,
        "area_hectares": 12.5,
        "irrigation_type": "rainfed",
        "farming_practice": "regenerative",
        "notes": "Demo farm. Rainfed smallholder plot trialling cover cropping.",
        "crops": [
            {
                "crop_code": "maize",
                "growth_stage": "flowering",
                "status": "growing",
                "is_primary": True,
                "planted_days_ago": 78,
                "season_days": 150,
                "area_hectares": 9.0,
            },
            {
                "crop_code": "common_bean",
                "growth_stage": "vegetative",
                "status": "growing",
                "is_primary": False,
                "planted_days_ago": 45,
                "season_days": 95,
                "area_hectares": 3.5,
            },
        ],
    },
    {
        "slug": "ames-soybean-quarter",
        "name": "Ames North Quarter",
        # Ames, Iowa — US corn belt.
        "latitude": 42.0308,
        "longitude": -93.6319,
        "country_code": "US",
        "region": "Iowa",
        "elevation_m": 287.0,
        "area_hectares": 64.0,
        "irrigation_type": "none",
        "farming_practice": "conventional",
        "notes": "Demo farm. Corn-belt quarter section on a maize/soybean rotation.",
        "crops": [
            {
                "crop_code": "soybean",
                "growth_stage": "fruiting",
                "status": "growing",
                "is_primary": True,
                "planted_days_ago": 92,
                "season_days": 140,
                "area_hectares": 64.0,
            },
        ],
    },
]


def seed_demo_farms(target: InMemoryStore | None = None) -> int:
    """Create the demo farms and their plantings. Returns how many farms were added.

    Idempotent: ids are derived from each farm's slug, so a farm that already
    exists is left untouched and repeated startups add nothing. A farm the user
    has since soft-deleted is also left alone rather than resurrected.
    """
    target = target if target is not None else store

    if not target.crops:
        seed_crops(target)

    today = datetime.now(UTC).date()
    now = datetime.now(UTC)
    created = 0

    with target.lock:
        for spec in DEMO_FARMS:
            farm_id = demo_id("farm", spec["slug"])

            # Present in any form — live or soft-deleted — means already seeded.
            if farm_id in target.farms:
                continue

            target.farms[farm_id] = FarmRecord(
                id=farm_id,
                name=spec["name"],
                latitude=spec["latitude"],
                longitude=spec["longitude"],
                area_hectares=spec["area_hectares"],
                country_code=spec["country_code"],
                region=spec["region"],
                elevation_m=spec["elevation_m"],
                irrigation_type=spec["irrigation_type"],
                farming_practice=spec["farming_practice"],
                notes=spec["notes"],
                created_at=now,
                updated_at=now,
            )
            created += 1

            for planting in spec["crops"]:
                code = planting["crop_code"]
                crop = target.crops_by_code.get(code)
                if crop is None:
                    # A demo referencing a crop outside the catalog is a fixture
                    # bug, not a runtime failure — skip it rather than break boot.
                    logger.warning(
                        "demo_seed_unknown_crop",
                        extra={"farm": spec["slug"], "crop_code": code},
                    )
                    continue

                planted_on = today - timedelta(days=planting["planted_days_ago"])
                planting_id = demo_id("planting", f"{spec['slug']}:{code}")
                target.farm_crops[planting_id] = FarmCropRecord(
                    id=planting_id,
                    farm_id=farm_id,
                    crop_id=crop.id,
                    planting_date=planted_on,
                    expected_harvest_date=planted_on + timedelta(days=planting["season_days"]),
                    growth_stage=planting["growth_stage"],
                    area_hectares=planting["area_hectares"],
                    is_primary=planting["is_primary"],
                    status=planting["status"],
                    notes=None,
                    created_at=now,
                    updated_at=now,
                )

    if created:
        logger.info("demo_farms_seeded", extra={"count": created})

    return created
