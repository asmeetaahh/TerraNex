"""Crop suitability and regenerative practice ranking.

The two failures this engine exists to prevent both come from the same habit — reading
a soil property that might not be there.

**Crashing.** Every soil field became nullable when the real survey provider landed. The
previous code compared `soil.ph` against a crop range unconditionally, so an unavailable
provider took down the whole analysis with a `TypeError`.

**Fabricating.** The obvious fix — substitute a neutral 70 — is worse, because a
fabricated score is indistinguishable from a measured one and it moves a planting
decision. An unmeasured property is dropped from the weighting instead, and the
remaining components are renormalised.

The catalog arrives on the context for the same reason: the engine cannot read a store
or a database, so it ranks what it was given and nothing else.
"""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.engine import recommendations as rec
from app.engine.context import AnalysisContext, CropParameters, DailyPoint, SoilPoint
from app.engine.disease import DiseaseAssessment
from app.engine.scoring import INSUFFICIENT, factor, unknown_factor
from app.engine.soil import SoilAssessmentResult
from app.engine.water import WaterAssessment
from app.engine.weather import WeatherAssessment
from app.schemas.common import RiskLevel, ScoreBand

TODAY = date(2026, 8, 22)

LOAM = SoilPoint(
    ph=6.4,
    organic_carbon_pct=1.8,
    cec_cmol_kg=15.0,
    bulk_density_kg_dm3=1.3,
    sand_pct=40.0,
    silt_pct=40.0,
    clay_pct=20.0,
    texture_class="loam",
)


def a_crop(code: str, **kw) -> CropParameters:
    values = {
        "code": code,
        "name": code.replace("_", " ").title(),
        "category": "cereal",
        "season": "summer",
        "ph_min": 5.5,
        "ph_max": 7.5,
        "optimal_temp_min_c": 18.0,
        "optimal_temp_max_c": 32.0,
        "preferred_textures": ("loam", "silt_loam"),
        "water_need_mm_season": 600.0,
        "parameters_source": "crop",
    }
    values.update(kw)
    return CropParameters(**values)


CATALOG = (a_crop("maize"), a_crop("wheat"), a_crop("sorghum"), a_crop("rice"))


def a_day(offset: int, mean: float | None = 24.0, rain: float | None = 2.0) -> DailyPoint:
    return DailyPoint(
        day=TODAY + timedelta(days=offset),
        temp_min_c=18.0,
        temp_max_c=30.0,
        temp_mean_c=mean,
        precipitation_mm=rain,
    )


def a_context(**overrides) -> AnalysisContext:
    values = {
        "farm_id": "f",
        "latitude": -21.1775,
        "longitude": -47.8103,
        "timezone": "UTC",
        "today": TODAY,
        "daily": tuple(a_day(n) for n in range(-60, 1)),
        "soil": LOAM,
        "crop": a_crop("maize"),
        "catalog": CATALOG,
        "growth_stage": "flowering",
        "irrigation_type": "rainfed",
        "farming_practice": "conventional",
    }
    values.update(overrides)
    return AnalysisContext(**values)


def an_assessment(kind: str, sufficient: bool = True, **kw):
    scored = (factor("k", "K", 80.0, 1.0, "measured"),)
    unscored = (unknown_factor("k", "K", ["evidence"]),)
    common = {"score": 20.0, "factors": scored if sufficient else unscored}
    common.update(kw)
    if kind == "water":
        return WaterAssessment(sufficient=sufficient, level=RiskLevel.low, **common)
    if kind == "disease":
        return DiseaseAssessment(sufficient=sufficient, level=RiskLevel.low, **common)
    if kind == "weather":
        return WeatherAssessment(level=RiskLevel.low, **common)
    return SoilAssessmentResult(
        sufficient=sufficient,
        score=80.0,
        band=ScoreBand.good,
        factors=scored if sufficient else unscored,
        **{k: v for k, v in kw.items() if k != "score"},
    )


def practices(**kw):
    context = kw.pop("context", a_context())
    return rec.regenerative_recommendations(
        context,
        kw.pop("soil", an_assessment("soil")),
        kw.pop("water", an_assessment("water")),
        kw.pop("disease", an_assessment("disease")),
        kw.pop("weather", an_assessment("weather")),
        **kw,
    )


# --------------------------------------------------------------------------
# Ranking, ties, limit
# --------------------------------------------------------------------------


def test_crops_are_ranked_best_first() -> None:
    result = rec.crop_recommendations(a_context(), limit=10)

    scores = [s.score for s in result]

    assert scores == sorted(scores, reverse=True)
    assert [s.rank for s in result] == list(range(1, len(result) + 1))


def test_ties_are_broken_by_crop_code_not_catalog_order() -> None:
    """Identical crops under different codes must rank alphabetically, so the order
    does not depend on how the catalog happened to be inserted."""
    identical = (a_crop("zulu"), a_crop("alpha"), a_crop("mike"))

    forward = rec.crop_recommendations(a_context(catalog=identical), limit=10)
    reversed_catalog = rec.crop_recommendations(
        a_context(catalog=tuple(reversed(identical))), limit=10
    )

    assert [s.code for s in forward] == ["alpha", "mike", "zulu"]
    assert [s.code for s in forward] == [s.code for s in reversed_catalog]


def test_the_limit_is_respected() -> None:
    assert len(rec.crop_recommendations(a_context(), limit=2)) == 2
    assert len(rec.crop_recommendations(a_context(), limit=100)) == len(CATALOG)


def test_a_limit_of_zero_returns_nothing() -> None:
    assert rec.crop_recommendations(a_context(), limit=0) == ()


def test_the_planted_crop_is_marked_as_current() -> None:
    result = rec.crop_recommendations(a_context(crop=a_crop("wheat")), limit=10)

    current = [s.code for s in result if s.is_current_crop]

    assert current == ["wheat"]


def test_a_better_matched_crop_outranks_a_worse_one() -> None:
    """The ranking has to respond to the site, not just produce an order."""
    catalog = (
        a_crop("suited", ph_min=6.0, ph_max=7.0, preferred_textures=("loam",)),
        a_crop("unsuited", ph_min=3.5, ph_max=4.2, preferred_textures=("sand",)),
    )

    result = rec.crop_recommendations(a_context(catalog=catalog), limit=10)

    assert result[0].code == "suited"
    assert result[0].score > result[1].score


# --------------------------------------------------------------------------
# Empty catalog — the database-path regression, at engine level
# --------------------------------------------------------------------------


def test_an_empty_catalog_yields_no_suggestions() -> None:
    """The engine proposes only what it was given. It cannot reach for a store, which
    is precisely what made the database path silently return nothing."""
    assert rec.crop_recommendations(a_context(catalog=()), limit=5) == ()


def test_a_catalog_entry_without_a_code_is_skipped() -> None:
    result = rec.crop_recommendations(a_context(catalog=(CropParameters(),)), limit=5)

    assert result == ()


def test_the_whole_catalog_can_be_ranked() -> None:
    big = tuple(a_crop(f"crop_{n:02d}") for n in range(26))

    result = rec.crop_recommendations(a_context(catalog=big), limit=100)

    assert len(result) == 26
    assert len({s.code for s in result}) == 26


# --------------------------------------------------------------------------
# Unavailable soil — the crash regression
# --------------------------------------------------------------------------


def test_an_entirely_unavailable_soil_does_not_crash() -> None:
    """Regression for the 500. With `SOIL_FALLBACK_TO_SIMULATION=false` and the
    provider down, every soil field arrives as None."""
    result = rec.crop_recommendations(a_context(soil=SoilPoint()), limit=5)

    assert len(result) > 0, "climate alone is still enough to rank crops"
    assert all(0 <= s.score <= 100 for s in result)


def test_unavailable_soil_is_declared_rather_than_scored() -> None:
    result = rec.crop_recommendations(a_context(soil=SoilPoint()), limit=1)
    ph = next(f for f in result[0].factors if f.key == "ph_match")
    texture = next(f for f in result[0].factors if f.key == "texture_match")

    assert ph.weight == 0.0
    assert texture.weight == 0.0
    assert INSUFFICIENT in ph.explanation
    assert INSUFFICIENT in texture.explanation


def test_no_neutral_value_is_invented_for_a_missing_property() -> None:
    """The tempting wrong fix. A substituted 70 is indistinguishable from a measured
    70, and it moves a planting decision."""
    measured = rec.crop_recommendations(a_context(), limit=1)[0]
    unmeasured = rec.crop_recommendations(a_context(soil=SoilPoint()), limit=1)[0]

    assert all(f.score != 70.0 or f.weight == 0.0 for f in unmeasured.factors)
    # The remaining components carry the whole weight rather than being diluted.
    assert sum(f.weight for f in unmeasured.factors if f.weight > 0) < sum(
        f.weight for f in measured.factors if f.weight > 0
    )


@pytest.mark.parametrize(
    ("absent", "expected_key"),
    [("ph", "ph_match"), ("texture_class", "texture_match")],
)
def test_one_missing_soil_property_does_not_disturb_the_others(
    absent: str, expected_key: str
) -> None:
    partial = replace(LOAM, **{absent: None})

    result = rec.crop_recommendations(a_context(soil=partial), limit=1)[0]

    assert next(f for f in result.factors if f.key == expected_key).weight == 0.0
    assert next(f for f in result.factors if f.key == "temperature_match").weight > 0


def test_missing_weather_leaves_climate_components_unassessed() -> None:
    blank = tuple(DailyPoint(day=TODAY + timedelta(days=n)) for n in range(-60, 1))

    result = rec.crop_recommendations(a_context(daily=blank), limit=1)[0]

    assert next(f for f in result.factors if f.key == "temperature_match").weight == 0.0
    assert next(f for f in result.factors if f.key == "water_match").weight == 0.0


def test_a_crop_with_nothing_assessable_is_not_ranked() -> None:
    """Ranking a crop no component could evaluate would be ordering by nothing."""
    blank = tuple(DailyPoint(day=TODAY + timedelta(days=n)) for n in range(-60, 1))
    featureless = CropParameters(code="unknown_crop", name="Unknown")

    result = rec.crop_recommendations(
        a_context(daily=blank, soil=SoilPoint(), catalog=(featureless,)), limit=5
    )

    assert result == ()


def test_rainfall_is_annualised_only_over_days_that_reported() -> None:
    """Counting unmeasured days as dry would bias every ranking toward
    drought-tolerant crops."""
    half_measured = tuple(a_day(n, rain=(2.0 if n % 2 == 0 else None)) for n in range(-60, 1))

    full = rec.site_conditions(a_context()).seasonal_rainfall_mm
    partial = rec.site_conditions(a_context(daily=half_measured)).seasonal_rainfall_mm

    assert full is not None and partial is not None
    assert partial == pytest.approx(full, rel=0.05)


# --------------------------------------------------------------------------
# Regenerative practices
# --------------------------------------------------------------------------


def test_practices_are_ranked_and_limited() -> None:
    result = practices(limit=3)

    assert len(result) == 3
    assert [p.rank for p in result] == [1, 2, 3]
    assert [p.relevance for p in result] == sorted([p.relevance for p in result], reverse=True)


def test_a_depleted_soil_promotes_carbon_building_practices() -> None:
    depleted = replace(LOAM, organic_carbon_pct=0.6)

    result = practices(context=a_context(soil=depleted), limit=7)
    healthy = practices(limit=7)

    def score(items, code):
        return next(p.relevance for p in items if p.code == code)

    assert score(result, "compost_application") > score(healthy, "compost_application")


def test_an_acidic_soil_promotes_liming() -> None:
    acidic = replace(LOAM, ph=4.8)

    result = practices(context=a_context(soil=acidic), limit=7)

    assert score_of(result, "liming") > score_of(practices(limit=7), "liming")


def score_of(items, code):
    return next(p.relevance for p in items if p.code == code)


def test_an_unmeasured_signal_drops_out_rather_than_defaulting() -> None:
    """An unmeasured soil is not a soil without problems. Liming targets acidity
    alone, so with no pH there is nothing to rank it on."""
    result = practices(context=a_context(soil=SoilPoint()), limit=10)

    assert "liming" not in {p.code for p in result}
    assert result, "practices driven by weather and water signals still rank"


def test_an_unassessed_risk_does_not_read_as_low_risk() -> None:
    """A disease score of zero means "no pressure" only when disease was assessed."""
    assessed = practices(disease=an_assessment("disease", score=80.0), limit=7)
    unassessed = practices(disease=an_assessment("disease", sufficient=False), limit=7)

    assert score_of(assessed, "crop_rotation") != score_of(unassessed, "crop_rotation")


def test_a_regenerative_farm_is_discounted_but_still_advised() -> None:
    conventional = practices(limit=7)
    regenerative = practices(context=a_context(farming_practice="regenerative"), limit=7)

    assert score_of(regenerative, "mulching") < score_of(conventional, "mulching")
    assert len(regenerative) == len(conventional)


def test_practice_considerations_come_from_measured_limitations() -> None:
    constrained = an_assessment("soil", limitations=("pH 4.8 is below range",))

    result = practices(soil=constrained, limit=1)

    assert result[0].considerations == ("pH 4.8 is below range",)


def test_unmeasured_soil_does_not_claim_there_are_no_constraints() -> None:
    result = practices(soil=an_assessment("soil", sufficient=False), limit=1)

    assert "measured" in result[0].considerations[0]


def test_a_rationale_cites_only_measurements_that_exist() -> None:
    result = practices(
        context=a_context(soil=SoilPoint()),
        water=an_assessment("water", sufficient=False),
        limit=1,
    )

    assert INSUFFICIENT in result[0].rationale
    assert "None" not in result[0].rationale


def test_every_practice_carries_actionable_steps() -> None:
    assert all(p.steps for p in practices(limit=10))


# --------------------------------------------------------------------------
# Determinism and global neutrality
# --------------------------------------------------------------------------


def test_the_same_context_produces_identical_crop_rankings() -> None:
    assert rec.crop_recommendations(a_context()) == rec.crop_recommendations(a_context())


def test_the_same_context_produces_identical_practice_rankings() -> None:
    assert practices() == practices()


def test_repeated_evaluation_never_varies() -> None:
    first = rec.crop_recommendations(a_context(), limit=4)

    assert all(rec.crop_recommendations(a_context(), limit=4) == first for _ in range(5))


@pytest.mark.parametrize(
    "latitude",
    [
        -21.1775,
        45.0453,
        68.9678,
        19.997,
        34.7578,
        -29.1211,
        24.0908,
        11.5936,
        29.6103,
        24.1917,
        3.5833,
        24.6877,
    ],
)
def test_identical_conditions_rank_identically_at_every_brics_latitude(
    latitude: float,
) -> None:
    """Suitability reads soil and weather, never a coordinate — so the same site
    conditions must produce the same ranking anywhere on Earth."""
    baseline = rec.crop_recommendations(a_context(latitude=-21.1775), limit=10)
    here = rec.crop_recommendations(a_context(latitude=latitude), limit=10)

    assert [s.code for s in here] == [s.code for s in baseline]
    assert [s.score for s in here] == [s.score for s in baseline]


def test_practice_ranking_is_location_independent() -> None:
    north = practices(context=a_context(latitude=52.0, longitude=13.0), limit=10)
    south = practices(context=a_context(latitude=-33.0, longitude=18.0), limit=10)

    assert [p.code for p in north] == [p.code for p in south]


def test_scores_stay_in_range() -> None:
    assert all(0.0 <= s.score <= 100.0 for s in rec.crop_recommendations(a_context(), limit=10))
    assert all(0.0 <= p.relevance <= 100.0 for p in practices(limit=10))


def test_the_engine_module_imports_nothing_impure() -> None:
    from tests.unit.engine.test_engine_is_pure import violations

    source = rec.__file__ or ""
    assert source
    with open(source, encoding="utf-8") as handle:
        assert violations(handle.read()) == []
