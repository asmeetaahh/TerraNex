"""Process-local in-memory store — Phase 3 only.

This exists so farm and crop CRUD behaves correctly end-to-end before SQLAlchemy and
Postgres land. It is deliberately minimal and has the limits you would expect:

* data is lost on restart,
* it is not shared between worker processes,
* there are no transactions.

It is replaced wholesale in the persistence phase. Nothing outside `app.services`
should import it, so that swap touches only the service layer.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.schemas.analysis import AnalysisRun
from app.schemas.crop import Crop
from app.schemas.image import CropImage


@dataclass
class FarmRecord:
    """Stored farm state. Derived fields (`crop_count`, `has_analysis`) are computed
    at read time rather than stored, so they cannot go stale."""

    id: UUID
    name: str
    latitude: float
    longitude: float
    area_hectares: float | None
    country_code: str | None
    region: str | None
    elevation_m: float | None
    irrigation_type: str
    farming_practice: str
    notes: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    # Set server-side from the resolved identity, never from a request body. Optional
    # so records written before ownership existed still construct.
    user_id: UUID | None = None


@dataclass
class FarmCropRecord:
    """Stored planting state."""

    id: UUID
    farm_id: UUID
    crop_id: UUID
    planting_date: object | None
    expected_harvest_date: object | None
    growth_stage: str
    area_hectares: float | None
    is_primary: bool
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class InMemoryStore:
    """The whole Phase 3 dataset. Insertion order is preserved by `dict`, which is
    what keeps list endpoints stably ordered."""

    crops: dict[UUID, Crop] = field(default_factory=dict)
    crops_by_code: dict[str, Crop] = field(default_factory=dict)
    farms: dict[UUID, FarmRecord] = field(default_factory=dict)
    farm_crops: dict[UUID, FarmCropRecord] = field(default_factory=dict)
    analysis_runs: dict[UUID, AnalysisRun] = field(default_factory=dict)
    crop_images: dict[UUID, CropImage] = field(default_factory=dict)

    # Guards the mutating paths. Uvicorn runs the event loop in one thread, but
    # sync endpoints and tests can touch the store from a worker thread.
    lock: threading.RLock = field(default_factory=threading.RLock)

    def reset(self) -> None:
        """Clear everything except the seeded crop catalog."""
        with self.lock:
            self.farms.clear()
            self.farm_crops.clear()
            self.analysis_runs.clear()
            self.crop_images.clear()

    # ---- farms ----

    def live_farms(self, user_id: UUID | None = None) -> list[FarmRecord]:
        """Live farms, optionally only those owned by `user_id`.

        A null owner belongs to the pre-ownership era and is visible only when no
        particular owner is asked for.
        """
        farms = [f for f in self.farms.values() if f.deleted_at is None]
        if user_id is None:
            return farms
        return [f for f in farms if f.user_id == user_id]

    def get_farm(self, farm_id: UUID, user_id: UUID | None = None) -> FarmRecord | None:
        record = self.farms.get(farm_id)
        if record is None or record.deleted_at is not None:
            return None
        if user_id is not None and record.user_id != user_id:
            return None
        return record

    # ---- plantings ----

    def crops_for_farm(self, farm_id: UUID) -> list[FarmCropRecord]:
        return [c for c in self.farm_crops.values() if c.farm_id == farm_id]

    # ---- analyses ----

    def runs_for_farm(self, farm_id: UUID) -> list[AnalysisRun]:
        """Newest first."""
        runs = [r for r in self.analysis_runs.values() if r.farm_id == farm_id]
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def latest_run(self, farm_id: UUID) -> AnalysisRun | None:
        runs = self.runs_for_farm(farm_id)
        return runs[0] if runs else None

    # ---- images ----

    def images_for_farm(self, farm_id: UUID) -> list[CropImage]:
        """Newest first."""
        images = [i for i in self.crop_images.values() if i.farm_id == farm_id]
        return sorted(images, key=lambda i: i.uploaded_at, reverse=True)


store = InMemoryStore()
