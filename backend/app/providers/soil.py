"""Soil properties, from ISRIC SoilGrids or a declared simulation.

SoilGrids is a global 250 m gridded prediction of soil properties, free and keyless,
which is why it was chosen: a farm anywhere on Earth resolves without provisioning
anything. It is a *model* rather than a survey — it predicts what a soil pit would
probably find — and that is a fair basis for advice as long as the response says where
the numbers came from, which `DataSourceMeta` enforces.

Two implementations share one interface, exactly as weather and geocoding do:

* :func:`soilgrids_soil` — the real global source.
* :func:`simulated_soil` — the deterministic simulator, used offline and in tests.

**Coverage is uneven and that is expected.** SoilGrids returns no value for open water,
ice, and some steep or unmapped terrain. A missing property arrives as `None` and stays
`None`; nothing here substitutes a plausible number for a place the model declined to
predict, because a fabricated soil produces real irrigation and fertiliser advice for a
real field.
"""

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import (
    ProviderCallError,
    ProviderResult,
    SoilObservation,
)
from app.providers.cache import build_key, get_cache
from app.providers.http import get_json
from app.services.simulation import simulate_soil

logger = get_logger(__name__)

SOILGRIDS_SOURCE = "isric-soilgrids"
SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"

#: The depth interval TerraNex reports. Matches `SoilProfile.depth_cm` and the profile
#: depth the water balance re-scales from, so the three cannot drift apart.
DEPTH_LABEL = "0-30cm"

#: Properties requested, mapped to the field they populate.
#:
#: `phh2o`  pH in water          `soc`   soil organic carbon
#: `clay` `sand` `silt`          particle size fractions
#: `cec`    cation exchange      `nitrogen`, `bdod` bulk density
PROPERTY_FIELDS: dict[str, str] = {
    "phh2o": "ph",
    "soc": "organic_carbon_pct",
    "nitrogen": "nitrogen_g_kg",
    "cec": "cec_cmol_kg",
    "bdod": "bulk_density_kg_dm3",
    "sand": "sand_pct",
    "silt": "silt_pct",
    "clay": "clay_pct",
}

#: Divisor turning SoilGrids' integer storage units into the units TerraNex reports.
#:
#: SoilGrids stores everything as integers to keep the rasters compact, and publishes
#: the divisor per layer as `unit_measures.d_factor`. That value is preferred when the
#: response carries it; these are the documented defaults for when it does not.
#:
#:   phh2o     pH x 10                 -> 10
#:   soc       dg/kg                   -> 100 gives percent
#:   nitrogen  cg/kg                   -> 100 gives g/kg
#:   cec       mmol(c)/kg              -> 10  gives cmol/kg
#:   bdod      cg/cm3                  -> 100 gives kg/dm3
#:   sand/silt/clay  g/kg              -> 10  gives percent
DEFAULT_DIVISORS: dict[str, float] = {
    "phh2o": 10.0,
    "soc": 100.0,
    "nitrogen": 100.0,
    "cec": 10.0,
    "bdod": 100.0,
    "sand": 10.0,
    "silt": 10.0,
    "clay": 10.0,
}

#: Physically possible ranges. A value outside these is a unit error or corrupt data,
#: not a remarkable soil, and is discarded rather than propagated into a score.
PLAUSIBLE_RANGE: dict[str, tuple[float, float]] = {
    "ph": (2.0, 11.0),
    "organic_carbon_pct": (0.0, 60.0),
    "nitrogen_g_kg": (0.0, 100.0),
    "cec_cmol_kg": (0.0, 200.0),
    "bulk_density_kg_dm3": (0.1, 2.6),
    "sand_pct": (0.0, 100.0),
    "silt_pct": (0.0, 100.0),
    "clay_pct": (0.0, 100.0),
}


def _cache():
    return get_cache("soil", settings.CACHE_TTL_SOIL_S)


def _numeric(value: Any) -> float | None:
    """A usable number, or None. `bool` is excluded because it subclasses `int`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _divisor(layer: dict[str, Any], property_name: str) -> float:
    """The layer's own conversion factor, falling back to the documented default."""
    units = layer.get("unit_measures")
    if isinstance(units, dict):
        factor = _numeric(units.get("d_factor"))
        if factor and factor > 0:
            return factor
    return DEFAULT_DIVISORS.get(property_name, 1.0)


def _mean_at_depth(layer: dict[str, Any]) -> Any:
    """The mean value for the reported depth interval, if the layer carries one."""
    depths = layer.get("depths")
    if not isinstance(depths, list):
        return None
    for depth in depths:
        if not isinstance(depth, dict):
            continue
        if depth.get("label") != DEPTH_LABEL:
            continue
        values = depth.get("values")
        if isinstance(values, dict):
            return values.get("mean")
    return None


def parse_soilgrids(payload: dict[str, Any]) -> SoilObservation:
    """Map a SoilGrids response onto a normalised observation.

    Deliberately forgiving about *shape* and strict about *values*. A layer TerraNex
    does not recognise is ignored, a missing depth interval yields `None`, and a value
    outside its physical range is discarded — but nothing is ever substituted.
    """
    properties = payload.get("properties")
    layers = properties.get("layers") if isinstance(properties, dict) else None
    if not isinstance(layers, list):
        return SoilObservation()

    values: dict[str, float] = {}
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        name = layer.get("name")
        field = PROPERTY_FIELDS.get(name) if isinstance(name, str) else None
        if field is None:
            continue

        raw = _numeric(_mean_at_depth(layer))
        if raw is None:
            continue

        converted = raw / _divisor(layer, name)
        low, high = PLAUSIBLE_RANGE.get(field, (float("-inf"), float("inf")))
        if not low <= converted <= high:
            logger.warning(
                "soilgrids_value_out_of_range",
                extra={"property": name, "raw": raw, "converted": converted},
            )
            continue
        values[field] = converted

    return _with_derived(SoilObservation(**values))


def _with_derived(observation: SoilObservation) -> SoilObservation:
    """Fill in texture class and water-holding capacity from particle sizes.

    Both are derived rather than reported: SoilGrids publishes fractions, and the USDA
    class and available-water estimate follow from them by the same functions the
    simulator uses. Neither is invented — with no fractions, both stay `None`.
    """
    from app.services.simulation import (
        _AVAILABLE_WATER_FRACTION,
        _ROOT_ZONE_MM,
        usda_texture_class,
    )

    sand, silt, clay = observation.sand_pct, observation.silt_pct, observation.clay_pct
    if sand is None or silt is None or clay is None:
        return observation

    total = sand + silt + clay
    if total <= 0:
        return observation

    # SoilGrids fractions are predicted independently and need not sum to exactly 100.
    sand, silt, clay = (100.0 * v / total for v in (sand, silt, clay))
    texture = usda_texture_class(sand, silt, clay)

    observation.sand_pct = round(sand, 1)
    observation.silt_pct = round(silt, 1)
    observation.clay_pct = round(clay, 1)
    observation.texture_class = texture
    observation.water_holding_capacity_mm = round(
        _AVAILABLE_WATER_FRACTION[texture] * _ROOT_ZONE_MM, 1
    )
    return observation


async def soilgrids_soil(latitude: float, longitude: float) -> ProviderResult[SoilObservation]:
    """Soil at a point from ISRIC SoilGrids.

    Cached for a long TTL: soil does not change on any timescale this product cares
    about, so a repeat request for the same field should never make a second call.
    """
    # Rounded to about 100 m, which is finer than the 250 m grid the source publishes,
    # so repeat requests for one field coalesce without merging neighbouring fields.
    key = build_key(SOILGRIDS_SOURCE, round(latitude, 3), round(longitude, 3))

    async def fetch() -> ProviderResult[SoilObservation]:
        params: dict[str, Any] = {
            "lat": latitude,
            "lon": longitude,
            "depth": DEPTH_LABEL,
            "value": "mean",
            # Repeated key: every requested property shares the same query parameter.
            "property": list(PROPERTY_FIELDS),
        }
        try:
            payload = await get_json(SOILGRIDS_SOURCE, SOILGRIDS_URL, params)
        except ProviderCallError as exc:
            logger.warning(
                "soilgrids_unavailable",
                extra={"latitude": latitude, "longitude": longitude, "reason": exc.reason},
            )
            return ProviderResult.unavailable(SOILGRIDS_SOURCE, exc.reason)

        return ProviderResult.live(
            parse_soilgrids(payload),
            SOILGRIDS_SOURCE,
            "ISRIC SoilGrids 250 m prediction for the 0-30 cm interval. A model, not a "
            "laboratory result.",
        )

    # `get_or_fetch` re-stamps a hit as `cached` and never caches a failure, so a
    # provider that is down for one request is retried on the next rather than
    # remembered as broken for the whole thirty-day TTL.
    return await _cache().get_or_fetch(key, fetch)


def simulated_soil(latitude: float, longitude: float) -> ProviderResult[SoilObservation]:
    """The deterministic simulator, labelled as such.

    Stable for a coordinate and never presented as an observation — the `simulated`
    factory is the only way to construct this provenance.
    """
    simulated = simulate_soil(latitude, longitude)
    return ProviderResult.simulated(
        SoilObservation(
            ph=simulated.ph,
            organic_carbon_pct=simulated.organic_carbon_pct,
            nitrogen_g_kg=simulated.nitrogen_g_kg,
            cec_cmol_kg=simulated.cec_cmol_kg,
            bulk_density_kg_dm3=simulated.bulk_density_kg_dm3,
            sand_pct=simulated.sand_pct,
            silt_pct=simulated.silt_pct,
            clay_pct=simulated.clay_pct,
            texture_class=simulated.texture_class,
            water_holding_capacity_mm=simulated.water_holding_capacity_mm,
        ),
        "Simulated soil: particle sizes classified on the USDA texture triangle. "
        "Not a soil survey or laboratory result.",
    )


async def get_soil(latitude: float, longitude: float) -> ProviderResult[SoilObservation]:
    """Soil from whichever provider is configured."""
    if settings.SOIL_PROVIDER == "simulated":
        return simulated_soil(latitude, longitude)
    return await soilgrids_soil(latitude, longitude)
