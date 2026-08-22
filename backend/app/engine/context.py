"""The engine's input: everything a calculation may read, and nothing else.

`AnalysisContext` is deliberately **provider-agnostic**. It does not reference
`app.providers`, `app.db` or any service, which is what lets the engine stay pure and
what lets a future provider be swapped without touching a single calculation.

The adapter that turns live provider data into a context lives in the *service* layer,
not here. Putting it in this module would mean importing `environment_service`, which
imports `app.providers` — and the purity guard would fail immediately. That boundary is
the point, not an inconvenience.

Every measurement is `float | None`. `None` means **unknown**, never zero: an absent
rainfall reading must not invent a drought, and an absent temperature must not be
compared against a threshold. Calculations skip unknowns and report what was missing.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from app.engine.version import ENGINE_VERSION

# Coordinates are rounded before hashing so that floating-point noise in the sixth
# decimal — about 10 cm — cannot produce a different hash for the same farm. Six
# decimals is also what the `farms` table stores, so this cannot lose precision that
# was ever persisted.
COORDINATE_PRECISION = 6

# Measurements are rounded before hashing for the same reason. Three decimals is far
# finer than any provider reports.
MEASUREMENT_PRECISION = 3


@dataclass(frozen=True, slots=True)
class DailyPoint:
    """One day of weather. Mirrors what providers supply, without importing them."""

    day: date
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    temp_mean_c: float | None = None
    humidity_pct: float | None = None
    precipitation_mm: float | None = None
    precipitation_hours: float | None = None
    wind_kmh: float | None = None
    et0_mm: float | None = None
    radiation_mj_m2: float | None = None


@dataclass(frozen=True, slots=True)
class HourlyPoint:
    """One hourly step. Hourly resolution is what disease rules need — a daily mean
    hides the ten-hour humid night that actually drives infection."""

    at: datetime
    temperature_c: float | None = None
    humidity_pct: float | None = None
    precipitation_mm: float | None = None
    wind_kmh: float | None = None
    soil_moisture_m3m3: float | None = None


@dataclass(frozen=True, slots=True)
class SoilPoint:
    """Soil properties at the farm, from a provider or a declared simulation."""

    ph: float | None = None
    organic_carbon_pct: float | None = None
    nitrogen_g_kg: float | None = None
    cec_cmol_kg: float | None = None
    bulk_density_kg_dm3: float | None = None
    sand_pct: float | None = None
    silt_pct: float | None = None
    clay_pct: float | None = None
    texture_class: str | None = None
    water_holding_capacity_mm: float | None = None

    #: Volumetric available water, theta_FC - theta_WP. Set only when a provider
    #: reports both limits directly; otherwise the engine derives it from texture.
    #: Kept separate from `water_holding_capacity_mm` because that figure is already
    #: multiplied by a fixed profile depth and cannot be re-scaled to a crop's roots.
    available_water_fraction: float | None = None


@dataclass(frozen=True, slots=True)
class CropParameters:
    """Everything the engine knows about the planted crop.

    Two sources feed this, and the split matters. The catalog fields
    (`optimal_temp_max_c`, `ph_min`, …) come from the `crops` table and are published
    through the frozen `Crop` schema. The FAO-56 fields (`kc_by_stage`, `root_depth_m`,
    `depletion_fraction`) come from `app/rules/crop_coefficients.yaml`, because the
    frozen contract has nowhere to carry them and adding them would change
    `contracts/openapi.json`.

    `parameters_source` records which happened, so a run can say whether it used a
    crop's own coefficients or fell back to its category's defaults.
    """

    code: str | None = None
    name: str | None = None
    category: str | None = None
    season: str | None = None

    # ---- FAO-56, from the ruleset ----
    kc_by_stage: Mapping[str, float] = field(default_factory=dict)
    root_depth_m: float | None = None
    depletion_fraction: float | None = None

    # ---- catalog, from the crops table ----
    base_temp_c: float | None = None
    optimal_temp_min_c: float | None = None
    optimal_temp_max_c: float | None = None
    gdd_to_maturity: float | None = None
    water_need_mm_season: float | None = None
    ph_min: float | None = None
    ph_max: float | None = None
    preferred_textures: Sequence[str] = ()
    common_diseases: Sequence[str] = ()
    drought_tolerance: str | None = None

    parameters_source: str = "unknown"
    """One of `crop`, `category_default`, `global_default`, or `unknown`."""

    def kc_for(self, stage: str | None) -> float | None:
        """The crop coefficient for `stage`, or None when it is not known.

        Returns None rather than a plausible-looking default: a water balance computed
        against a guessed coefficient reports a deficit it cannot justify, and the
        caller needs to be able to mark that factor insufficient.
        """
        if stage is None:
            return None
        return self.kc_by_stage.get(stage)


@dataclass(frozen=True, slots=True)
class Range:
    """A closed interval a measurement must fall inside. Either end may be open."""

    low: float | None = None
    high: float | None = None

    def contains(self, value: float) -> bool:
        if self.low is not None and value < self.low:
            return False
        return not (self.high is not None and value > self.high)

    def describe(self, unit: str) -> str:
        if self.low is not None and self.high is not None:
            return f"{self.low:g}-{self.high:g}{unit}"
        if self.low is not None:
            return f"at or above {self.low:g}{unit}"
        if self.high is not None:
            return f"at or below {self.high:g}{unit}"
        return f"any{unit}"


@dataclass(frozen=True, slots=True)
class RuleCondition:
    """One clause of a disease rule.

    `consecutive_hours` requires an unbroken run; `total_hours` counts matching hours
    wherever they fall; `growth_stage_at_least` gates on the crop's development. A rule
    is the conjunction of its clauses — every one must match.
    """

    type: str
    hours: int = 0
    temp_c: Range | None = None
    humidity_pct: Range | None = None
    precipitation_mm: Range | None = None
    stage: str | None = None


@dataclass(frozen=True, slots=True)
class DiseaseRule:
    """One pathogen's infection requirement, as data.

    Lives here rather than in the rules package because the engine must be able to read
    it without importing anything that touches a filesystem. The registry parses the
    YAML into these; the adapter attaches the applicable ones to the context.
    """

    id: str
    name: str
    pathogen: str | None
    crops: tuple[str, ...]
    conditions: tuple[RuleCondition, ...]
    threshold_hours: int
    saturation_hours: int
    preventive_actions: tuple[str, ...] = ()
    scouting_advice: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """One farm, one moment, one complete set of inputs.

    Construct it once per run and pass it down. No calculation reads anything outside
    it — not the clock, not settings, not the database.
    """

    # ---- identity and place ----
    farm_id: str
    latitude: float
    longitude: float
    timezone: str | None
    today: date

    # ---- observations ----
    daily: Sequence[DailyPoint] = ()
    hourly: Sequence[HourlyPoint] = ()
    vegetation: Sequence[tuple[date, float]] = ()
    soil: SoilPoint = field(default_factory=SoilPoint)

    # ---- the planting ----
    crop: CropParameters = field(default_factory=CropParameters)

    #: Disease rules applicable to the planted crop, resolved by the adapter. Empty
    #: when nothing is planted, which is why no pathogen can be named for a bare field.
    disease_rules: Sequence[DiseaseRule] = ()

    #: Every crop the recommender may propose, resolved by the adapter from whichever
    #: catalog is configured — the database when one is set, the in-memory store
    #: otherwise. The engine cannot read either, so an empty catalog here means the
    #: engine proposes nothing rather than silently reading a store that is not the
    #: source of truth.
    catalog: Sequence[CropParameters] = ()

    growth_stage: str | None = None
    irrigation_type: str | None = None
    farming_practice: str | None = None
    planting_date: date | None = None
    expected_harvest_date: date | None = None
    area_hectares: float | None = None

    # ---- provenance ----
    engine_version: str = ENGINE_VERSION
    ruleset_version: str = ""

    # ---- windows ----

    def history(self, days: int) -> list[DailyPoint]:
        """The `days` days immediately before today, oldest first."""
        start = _shift(self.today, -days)
        return [d for d in self.daily if start <= d.day < self.today]

    def forecast(self, days: int) -> list[DailyPoint]:
        """Today plus the following `days - 1` days."""
        end = _shift(self.today, days)
        return [d for d in self.daily if self.today <= d.day < end]

    def window(self, back: int, forward: int) -> list[DailyPoint]:
        """History and forecast as one continuous series, oldest first.

        The water balance iterates over exactly this: it needs the depletion the past
        left behind before it can say what the forecast will do.
        """
        return self.history(back) + self.forecast(forward)

    def hourly_between(self, start: datetime, end: datetime) -> list[HourlyPoint]:
        return [h for h in self.hourly if start <= h.at < end]


def _shift(day: date, days: int) -> date:
    from datetime import timedelta

    return day + timedelta(days=days)


# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------


def _round(value: float | None, places: int) -> float | None:
    return None if value is None else round(value, places)


def canonical_inputs(context: AnalysisContext) -> dict:
    """The context reduced to exactly what can change a result.

    Anything absent from this dict is, by construction, something the engine promises
    not to depend on. `farm_id` is deliberately excluded: two farms at the same
    coordinates with the same crop and the same weather must produce the same analysis,
    and keying the cache on identity instead of inputs would miss that.
    """
    return {
        "engine_version": context.engine_version,
        "ruleset_version": context.ruleset_version,
        "latitude": _round(context.latitude, COORDINATE_PRECISION),
        "longitude": _round(context.longitude, COORDINATE_PRECISION),
        "today": context.today.isoformat(),
        "growth_stage": context.growth_stage,
        "irrigation_type": context.irrigation_type,
        "farming_practice": context.farming_practice,
        # Only the codes: a catalog entry's own parameters cannot change a
        # recommendation without the set of offered codes changing too, and hashing
        # every field of twenty-six crops would dwarf the rest of the digest.
        "catalog": sorted(c.code for c in context.catalog if c.code),
        "planting_date": context.planting_date.isoformat() if context.planting_date else None,
        "expected_harvest_date": (
            context.expected_harvest_date.isoformat() if context.expected_harvest_date else None
        ),
        "area_hectares": _round(context.area_hectares, MEASUREMENT_PRECISION),
        "crop": {
            "code": context.crop.code,
            "category": context.crop.category,
            "kc_by_stage": {k: _round(v, 3) for k, v in sorted(context.crop.kc_by_stage.items())},
            "root_depth_m": _round(context.crop.root_depth_m, 3),
            "depletion_fraction": _round(context.crop.depletion_fraction, 3),
            "base_temp_c": _round(context.crop.base_temp_c, 3),
            "optimal_temp_min_c": _round(context.crop.optimal_temp_min_c, 3),
            "optimal_temp_max_c": _round(context.crop.optimal_temp_max_c, 3),
            "gdd_to_maturity": _round(context.crop.gdd_to_maturity, 3),
            "water_need_mm_season": _round(context.crop.water_need_mm_season, 3),
            "ph_min": _round(context.crop.ph_min, 3),
            "ph_max": _round(context.crop.ph_max, 3),
            "preferred_textures": sorted(context.crop.preferred_textures),
            "common_diseases": sorted(context.crop.common_diseases),
            "drought_tolerance": context.crop.drought_tolerance,
            "parameters_source": context.crop.parameters_source,
        },
        "soil": {
            "ph": _round(context.soil.ph, MEASUREMENT_PRECISION),
            "organic_carbon_pct": _round(context.soil.organic_carbon_pct, MEASUREMENT_PRECISION),
            "nitrogen_g_kg": _round(context.soil.nitrogen_g_kg, MEASUREMENT_PRECISION),
            "cec_cmol_kg": _round(context.soil.cec_cmol_kg, MEASUREMENT_PRECISION),
            "bulk_density_kg_dm3": _round(context.soil.bulk_density_kg_dm3, MEASUREMENT_PRECISION),
            "sand_pct": _round(context.soil.sand_pct, MEASUREMENT_PRECISION),
            "silt_pct": _round(context.soil.silt_pct, MEASUREMENT_PRECISION),
            "clay_pct": _round(context.soil.clay_pct, MEASUREMENT_PRECISION),
            "texture_class": context.soil.texture_class,
            "water_holding_capacity_mm": _round(
                context.soil.water_holding_capacity_mm, MEASUREMENT_PRECISION
            ),
            "available_water_fraction": _round(
                context.soil.available_water_fraction, MEASUREMENT_PRECISION
            ),
        },
        "daily": [
            {
                "day": d.day.isoformat(),
                "temp_min_c": _round(d.temp_min_c, MEASUREMENT_PRECISION),
                "temp_max_c": _round(d.temp_max_c, MEASUREMENT_PRECISION),
                "temp_mean_c": _round(d.temp_mean_c, MEASUREMENT_PRECISION),
                "humidity_pct": _round(d.humidity_pct, MEASUREMENT_PRECISION),
                "precipitation_mm": _round(d.precipitation_mm, MEASUREMENT_PRECISION),
                "precipitation_hours": _round(d.precipitation_hours, MEASUREMENT_PRECISION),
                "wind_kmh": _round(d.wind_kmh, MEASUREMENT_PRECISION),
                "et0_mm": _round(d.et0_mm, MEASUREMENT_PRECISION),
                "radiation_mj_m2": _round(d.radiation_mj_m2, MEASUREMENT_PRECISION),
            }
            for d in sorted(context.daily, key=lambda d: d.day)
        ],
        "hourly": [
            {
                "at": h.at.isoformat(),
                "temperature_c": _round(h.temperature_c, MEASUREMENT_PRECISION),
                "humidity_pct": _round(h.humidity_pct, MEASUREMENT_PRECISION),
                "precipitation_mm": _round(h.precipitation_mm, MEASUREMENT_PRECISION),
                "wind_kmh": _round(h.wind_kmh, MEASUREMENT_PRECISION),
                "soil_moisture_m3m3": _round(h.soil_moisture_m3m3, MEASUREMENT_PRECISION),
            }
            for h in sorted(context.hourly, key=lambda h: h.at)
        ],
        "vegetation": [
            [when.isoformat(), _round(value, MEASUREMENT_PRECISION)]
            for when, value in sorted(context.vegetation, key=lambda pair: pair[0])
        ],
    }


def inputs_hash(context: AnalysisContext) -> str:
    """A stable SHA-256 over everything that can change a result.

    Two calls with equal inputs return the same digest on any machine and in any
    process, which is what makes it usable as a cache key and as evidence that a stored
    run corresponds to the data it claims. `sort_keys` plus the tightest separators
    removes every source of formatting drift; `sha256` rather than `hash()` avoids
    Python's per-process string salt.
    """
    payload = json.dumps(
        canonical_inputs(context),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
