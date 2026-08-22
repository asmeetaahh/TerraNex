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


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """What a stored run needs to be found again by its inputs rather than by recency.

    The database keeps these as columns on `analysis_runs`; the store keeps them beside
    the run for the same reason. Without them the offline path can only answer "what was
    the last run for this farm", which is a different question — and returns a stale
    answer whenever the inputs have changed since.
    """

    inputs_hash: str
    engine_version: str
    ruleset_version: str


@dataclass(frozen=True, slots=True)
class ImageMetadata:
    """What a stored image needs in order to be diagnosed the same way twice.

    The database keeps `sha256` as a column on `crop_images`; the store keeps it here
    for the same reason — `CropImage` is published in the frozen contract and has no
    field for it.

    This previously lived in a module-global dict inside `image_service`, outside the
    store entirely. It was therefore not cleared by `reset()` and not carried by any
    storage swap, so re-analysing an image after a restart seeded the diagnosis from
    the image's id instead of its bytes and returned a *different* result for the same
    photograph.
    """

    sha256: str


@dataclass
class InMemoryStore:
    """The whole Phase 3 dataset. Insertion order is preserved by `dict`, which is
    what keeps list endpoints stably ordered."""

    crops: dict[UUID, Crop] = field(default_factory=dict)
    crops_by_code: dict[str, Crop] = field(default_factory=dict)
    farms: dict[UUID, FarmRecord] = field(default_factory=dict)
    farm_crops: dict[UUID, FarmCropRecord] = field(default_factory=dict)
    analysis_runs: dict[UUID, AnalysisRun] = field(default_factory=dict)
    #: Reproducibility metadata, keyed by run id. Held alongside rather than on the
    #: run because `AnalysisRun` is published in the frozen contract and has no
    #: field for any of it.
    run_metadata: dict[UUID, RunMetadata] = field(default_factory=dict)
    crop_images: dict[UUID, CropImage] = field(default_factory=dict)
    #: Content digests, keyed by image id. Held alongside rather than on the image
    #: because `CropImage` is published in the frozen contract and has no field for it.
    image_metadata: dict[UUID, ImageMetadata] = field(default_factory=dict)

    # Guards the mutating paths. Uvicorn runs the event loop in one thread, but
    # sync endpoints and tests can touch the store from a worker thread.
    lock: threading.RLock = field(default_factory=threading.RLock)

    def reset(self) -> None:
        """Clear everything except the seeded crop catalog."""
        with self.lock:
            self.farms.clear()
            self.farm_crops.clear()
            self.analysis_runs.clear()
            self.run_metadata.clear()
            self.crop_images.clear()
            self.image_metadata.clear()

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

    def record_run(self, run: AnalysisRun, metadata: RunMetadata) -> None:
        """Store a run together with the metadata that lets it be found by inputs."""
        with self.lock:
            self.analysis_runs[run.id] = run
            self.run_metadata[run.id] = metadata

    def find_cached_run(
        self,
        farm_id: UUID,
        inputs_hash: str,
        *,
        engine_version: str,
        ruleset_version: str,
        not_before: datetime | None = None,
    ) -> AnalysisRun | None:
        """A previous run of this exact analysis, if one is still usable.

        Mirrors `analysis_repo.find_cached_run` clause for clause, so the offline path
        and the persisted path answer the same question. Before this existed the store
        returned the most recent run regardless of inputs, which meant changing a
        farm's crop and re-analysing returned the old crop's score.
        """
        for run in self.runs_for_farm(farm_id):
            metadata = self.run_metadata.get(run.id)
            if metadata is None:
                # Written before metadata was recorded; not identifiable by inputs.
                continue
            if metadata.inputs_hash != inputs_hash:
                continue
            if metadata.engine_version != engine_version:
                continue
            if metadata.ruleset_version != ruleset_version:
                continue
            if not_before is not None and run.created_at < not_before:
                continue
            return run
        return None

    def runs_for_farm(self, farm_id: UUID) -> list[AnalysisRun]:
        """Newest first."""
        runs = [r for r in self.analysis_runs.values() if r.farm_id == farm_id]
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def latest_run(self, farm_id: UUID) -> AnalysisRun | None:
        runs = self.runs_for_farm(farm_id)
        return runs[0] if runs else None

    # ---- images ----

    def record_image(self, image: CropImage, metadata: ImageMetadata) -> None:
        """Store an image together with the digest that makes its diagnosis stable."""
        with self.lock:
            self.crop_images[image.id] = image
            self.image_metadata[image.id] = metadata

    def update_image(self, image: CropImage) -> None:
        """Replace a stored image in place, leaving its digest untouched.

        The bytes did not change, so neither may the digest — it is what makes a repeat
        diagnosis identical to the one it replaces.
        """
        with self.lock:
            self.crop_images[image.id] = image

    def image_digest(self, image_id: UUID) -> str | None:
        metadata = self.image_metadata.get(image_id)
        return metadata.sha256 if metadata is not None else None

    def images_for_farm(self, farm_id: UUID) -> list[CropImage]:
        """Newest first."""
        images = [i for i in self.crop_images.values() if i.farm_id == farm_id]
        return sorted(images, key=lambda i: i.uploaded_at, reverse=True)


store = InMemoryStore()
