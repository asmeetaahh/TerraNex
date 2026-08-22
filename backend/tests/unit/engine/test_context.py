"""The analysis context and its reproducibility hash.

The hash is the mechanism behind two product properties: a repeat request inside the
cache window returns the stored run instead of recomputing, and a persisted run can be
shown to correspond to the data it claims. Both fail silently if the hash is unstable
or if it ignores something that actually moves a score — so most of this file is about
what the digest does and does not respond to.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.engine.context import (
    AnalysisContext,
    CropParameters,
    DailyPoint,
    HourlyPoint,
    SoilPoint,
    canonical_inputs,
    inputs_hash,
)
from app.engine.version import ENGINE_VERSION

TODAY = date(2026, 8, 22)


def a_day(offset: int, **overrides) -> DailyPoint:
    values = {
        "temp_min_c": 18.0,
        "temp_max_c": 31.0,
        "temp_mean_c": 24.5,
        "humidity_pct": 62.0,
        "precipitation_mm": 1.2,
        "et0_mm": 5.4,
        "wind_kmh": 11.0,
    }
    values.update(overrides)
    return DailyPoint(day=TODAY + timedelta(days=offset), **values)


def a_context(**overrides) -> AnalysisContext:
    values = {
        "farm_id": "farm-1",
        "latitude": -21.1775,
        "longitude": -47.8103,
        "timezone": "UTC",
        "today": TODAY,
        "daily": tuple(a_day(offset) for offset in range(-30, 8)),
        "soil": SoilPoint(
            ph=6.2, sand_pct=40.0, silt_pct=40.0, clay_pct=20.0, texture_class="loam"
        ),
        "crop": CropParameters(code="maize", category="cereal", kc_by_stage={"flowering": 1.2}),
        "growth_stage": "flowering",
        "irrigation_type": "drip",
        "ruleset_version": "abc123",
    }
    values.update(overrides)
    return AnalysisContext(**values)


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------


def test_history_excludes_today() -> None:
    history = a_context().history(30)

    assert len(history) == 30
    assert all(d.day < TODAY for d in history)


def test_forecast_includes_today() -> None:
    forecast = a_context().forecast(7)

    assert len(forecast) == 7
    assert forecast[0].day == TODAY


def test_the_window_is_continuous_and_ordered() -> None:
    """The water balance iterates this series and carries depletion forward, so a gap
    or an out-of-order day would silently corrupt the running total."""
    window = a_context().window(30, 7)

    assert len(window) == 37
    days = [d.day for d in window]
    assert days == sorted(days)
    assert days[-1] - days[0] == timedelta(days=36)


def test_windows_are_empty_rather_than_raising_when_there_is_no_data() -> None:
    empty = a_context(daily=())

    assert empty.history(30) == []
    assert empty.forecast(7) == []


def test_hourly_slicing_is_half_open() -> None:
    start = datetime(2026, 8, 22, 0, 0, tzinfo=UTC)
    hours = tuple(HourlyPoint(at=start + timedelta(hours=n), humidity_pct=90.0) for n in range(24))

    sliced = a_context(hourly=hours).hourly_between(start, start + timedelta(hours=10))

    assert len(sliced) == 10


# --------------------------------------------------------------------------
# Crop parameters
# --------------------------------------------------------------------------


def test_kc_for_a_known_stage() -> None:
    crop = CropParameters(kc_by_stage={"flowering": 1.2, "germination": 0.3})

    assert crop.kc_for("flowering") == 1.2


def test_kc_for_an_unknown_stage_is_none_not_a_guess() -> None:
    """Returning a plausible default here would produce a water deficit the run could
    not justify. The caller needs to be able to mark the factor insufficient."""
    crop = CropParameters(kc_by_stage={"flowering": 1.2})

    assert crop.kc_for("maturity") is None
    assert crop.kc_for(None) is None


def test_a_context_with_no_crop_is_valid() -> None:
    """A farm with no planting is a supported state, not an error."""
    bare = a_context(crop=CropParameters(), growth_stage=None)

    assert bare.crop.code is None
    assert bare.crop.kc_for("flowering") is None
    assert inputs_hash(bare)


# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------


def test_the_same_context_hashes_identically() -> None:
    assert inputs_hash(a_context()) == inputs_hash(a_context())


def test_the_hash_is_a_sha256_digest() -> None:
    digest = inputs_hash(a_context())

    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


def test_day_order_does_not_change_the_hash() -> None:
    """Providers do not guarantee ordering, and a reordered response is the same data."""
    forward = a_context()
    reversed_days = a_context(daily=tuple(reversed(forward.daily)))

    assert inputs_hash(forward) == inputs_hash(reversed_days)


def test_identity_does_not_change_the_hash() -> None:
    """Two farms at the same place with the same crop and weather have the same
    analysis. Keying on identity would miss that and recompute needlessly."""
    assert inputs_hash(a_context(farm_id="farm-1")) == inputs_hash(a_context(farm_id="farm-2"))


def test_sub_millimetre_coordinate_noise_does_not_change_the_hash() -> None:
    """Float noise past the sixth decimal is below what the farms table stores."""
    jittered = a_context(latitude=-21.1775 + 1e-9)

    assert inputs_hash(a_context()) == inputs_hash(jittered)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latitude", 45.0453),
        ("longitude", 38.9818),
        ("today", date(2026, 8, 23)),
        ("growth_stage", "maturity"),
        ("irrigation_type", "rainfed"),
        ("ruleset_version", "def456"),
        ("engine_version", "9.9.9"),
    ],
)
def test_anything_that_moves_a_score_changes_the_hash(field: str, value) -> None:
    assert inputs_hash(a_context()) != inputs_hash(a_context(**{field: value}))


def test_changing_one_days_rainfall_changes_the_hash() -> None:
    changed = list(a_context().daily)
    changed[5] = a_day(-25, precipitation_mm=88.0)

    assert inputs_hash(a_context()) != inputs_hash(a_context(daily=tuple(changed)))


def test_changing_soil_changes_the_hash() -> None:
    drier = SoilPoint(ph=8.1, sand_pct=80.0, silt_pct=15.0, clay_pct=5.0, texture_class="sand")

    assert inputs_hash(a_context()) != inputs_hash(a_context(soil=drier))


def test_changing_crop_coefficients_changes_the_hash() -> None:
    """A ruleset edit must invalidate cached runs even when the weather is identical."""
    retuned = CropParameters(code="maize", category="cereal", kc_by_stage={"flowering": 1.35})

    assert inputs_hash(a_context()) != inputs_hash(a_context(crop=retuned))


def test_an_unknown_reading_is_not_the_same_as_zero() -> None:
    """The distinction the whole missing-data discipline rests on. If these hashed
    alike, a provider outage would be indistinguishable from a drought."""
    unknown = list(a_context().daily)
    unknown[3] = a_day(-27, precipitation_mm=None)
    zeroed = list(a_context().daily)
    zeroed[3] = a_day(-27, precipitation_mm=0.0)

    assert inputs_hash(a_context(daily=tuple(unknown))) != inputs_hash(
        a_context(daily=tuple(zeroed))
    )


def test_the_canonical_form_omits_identity() -> None:
    canonical = canonical_inputs(a_context())

    assert "farm_id" not in canonical
    assert canonical["engine_version"] == ENGINE_VERSION
    assert canonical["crop"]["code"] == "maize"


def test_the_canonical_form_is_json_serialisable() -> None:
    """It is hashed through `json.dumps` with `allow_nan=False`, so a NaN or a stray
    non-serialisable value would raise rather than silently produce a digest."""
    import json

    payload = json.dumps(canonical_inputs(a_context()), sort_keys=True, allow_nan=False)

    assert json.loads(payload)["today"] == "2026-08-22"
