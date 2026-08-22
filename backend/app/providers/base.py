"""Provider contracts: results, provenance, observations, and errors.

Two rules are enforced structurally here rather than left to call sites:

**A provider failure never propagates as an exception into a service.** Providers
return :class:`ProviderResult`, which always carries provenance. A caller that forgets
to check `ok` still gets a payload whose `meta.mode` says what happened.

**Provenance can only be constructed through the factories below.** There is no way to
hand-build a `DataSourceMeta` that claims `live` for simulated values, because the
simulator's factory only ever emits `simulated`.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Protocol

from app.schemas.common import DataMode, DataSourceMeta
from app.schemas.enums import SoilTexture

SIMULATED_SOURCE = "simulated"


# --------------------------------------------------------------------------
# Provenance factories
# --------------------------------------------------------------------------


def _meta(source: str, mode: DataMode, note: str | None) -> DataSourceMeta:
    return DataSourceMeta(source=source, mode=mode, fetched_at=datetime.now(UTC), note=note)


def live_meta(source: str, note: str | None = None) -> DataSourceMeta:
    """Real provider data, fetched during this request."""
    return _meta(source, DataMode.live, note)


def cached_meta(source: str, note: str | None = None) -> DataSourceMeta:
    """Real provider data, served from cache within its TTL."""
    return _meta(source, DataMode.cached, note or "Served from cache; originally a live fetch.")


def simulated_meta(note: str | None = None) -> DataSourceMeta:
    """Locally generated values. Never a real observation.

    The source is pinned to `"simulated"` and the mode to `DataMode.simulated`, so a
    caller cannot pass this off as provider data.
    """
    return _meta(
        SIMULATED_SOURCE,
        DataMode.simulated,
        note or "Generated locally by the deterministic simulator; not a real observation.",
    )


def unavailable_meta(source: str, note: str | None = None) -> DataSourceMeta:
    """The provider could not be reached and no substitute was used."""
    return _meta(source, DataMode.unavailable, note or f"{source} was unavailable.")


# --------------------------------------------------------------------------
# Result envelope
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ProviderResult[T]:
    """What every provider returns. `data` may be None only when `ok` is False."""

    data: T | None
    meta: DataSourceMeta
    ok: bool = True

    @classmethod
    def live(cls, data: T, source: str, note: str | None = None) -> "ProviderResult[T]":
        return cls(data=data, meta=live_meta(source, note), ok=True)

    @classmethod
    def cached(cls, data: T, source: str, note: str | None = None) -> "ProviderResult[T]":
        return cls(data=data, meta=cached_meta(source, note), ok=True)

    @classmethod
    def simulated(cls, data: T, note: str | None = None) -> "ProviderResult[T]":
        return cls(data=data, meta=simulated_meta(note), ok=True)

    @classmethod
    def unavailable(cls, source: str, note: str | None = None) -> "ProviderResult[T]":
        return cls(data=None, meta=unavailable_meta(source, note), ok=False)

    def with_mode(self, mode: DataMode) -> "ProviderResult[T]":
        """Re-stamp the mode, used when a cache layer serves a previously live fetch."""
        return ProviderResult(
            data=self.data,
            meta=DataSourceMeta(
                source=self.meta.source,
                mode=mode,
                fetched_at=self.meta.fetched_at,
                note=self.meta.note,
            ),
            ok=self.ok,
        )


# --------------------------------------------------------------------------
# Provider-internal errors
# --------------------------------------------------------------------------


class ProviderCallError(Exception):
    """A provider call failed. Converted into a ProviderResult before leaving the
    provider layer — services never see this."""

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"{source}: {reason}")


class ProviderTimeout(ProviderCallError):
    """The provider did not respond inside PROVIDER_TIMEOUT_S."""


class ProviderBadResponse(ProviderCallError):
    """The provider responded, but not with something usable."""


# --------------------------------------------------------------------------
# Normalised observations
#
# Providers map their own payloads onto these, so services and the risk engine
# never branch on which provider supplied the data.
# --------------------------------------------------------------------------


@dataclass(slots=True)
class DailyObservation:
    """One day of weather at a location. Mirrors what the analysis engine consumes."""

    date: date
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    temp_mean_c: float | None = None
    humidity_pct: float | None = None
    # Nullable like every other measurement: an absent reading means "not measured",
    # not "no rain". Defaulting it to 0.0 invented dry weather and biased the water
    # balance toward a false deficit. The frozen contract already permits null here.
    precipitation_mm: float | None = None
    precipitation_hours: float | None = None
    wind_kmh: float | None = None
    et0_mm: float | None = None
    radiation_mj_m2: float | None = None
    cloud_cover_pct: float | None = None
    condition: str | None = None


@dataclass(slots=True)
class HourlyObservation:
    """One hourly step. Hourly resolution is what disease rules need."""

    time: datetime
    temperature_c: float | None = None
    humidity_pct: float | None = None
    precipitation_mm: float | None = None
    wind_kmh: float | None = None
    et0_mm: float | None = None
    radiation_w_m2: float | None = None
    soil_moisture_m3m3: float | None = None
    soil_temperature_c: float | None = None


@dataclass(slots=True)
class CurrentObservation:
    """Conditions at the farm right now. Never fabricated — when the provider gives
    no current block, this stays None all the way to the response."""

    observed_at: datetime
    temperature_c: float
    feels_like_c: float | None = None
    humidity_pct: float | None = None
    precipitation_mm: float | None = None
    wind_speed_kmh: float | None = None
    wind_direction_deg: float | None = None
    cloud_cover_pct: float | None = None
    pressure_hpa: float | None = None
    condition: str | None = None
    condition_label: str | None = None


@dataclass(slots=True)
class WeatherObservations:
    """Everything a weather provider returns for one location."""

    latitude: float
    longitude: float
    timezone: str | None
    current: CurrentObservation | None
    daily: list[DailyObservation] = field(default_factory=list)
    hourly: list[HourlyObservation] = field(default_factory=list)


@dataclass(slots=True)
class GeocodeCandidate:
    """One resolved place. Coordinates come from the provider and are never derived
    from the query string."""

    name: str
    latitude: float
    longitude: float
    country: str | None = None
    country_code: str | None = None
    region: str | None = None
    elevation_m: float | None = None
    timezone: str | None = None
    population: int | None = None

    @property
    def display_name(self) -> str:
        parts = [self.name, self.region, self.country]
        return ", ".join(p for p in parts if p)


@dataclass(slots=True)
class SoilObservation:
    """Soil properties at one location, from a survey or a declared simulation.

    **Every field is optional.** A global soil database returns what it has sampled for
    a given point, and coverage is genuinely uneven — a profile with pH and texture but
    no cation exchange capacity is ordinary, not an error. The risk engine is built to
    score what is present and report what is not, so `None` must survive the whole way
    down rather than being defaulted at the boundary.

    Field names match the simulator's output exactly, so `to_soil_profile` maps either
    source without branching on which one produced it.
    """

    ph: float | None = None
    organic_carbon_pct: float | None = None
    nitrogen_g_kg: float | None = None
    cec_cmol_kg: float | None = None
    bulk_density_kg_dm3: float | None = None
    sand_pct: float | None = None
    silt_pct: float | None = None
    clay_pct: float | None = None
    texture_class: SoilTexture | None = None
    water_holding_capacity_mm: float | None = None


class SoilProvider(Protocol):
    """What any soil source must offer.

    Named so that a country substituting its own authoritative soil database implements
    a stated interface rather than guessing at one. The service layer depends on this
    shape, never on ISRIC's — which is what makes the swap a single new file.
    """

    source: str

    async def get_soil(self, latitude: float, longitude: float) -> ProviderResult[SoilObservation]:
        """Soil at a point. Never raises into a service; failure is an
        `unavailable` result carrying its own provenance."""
        ...
