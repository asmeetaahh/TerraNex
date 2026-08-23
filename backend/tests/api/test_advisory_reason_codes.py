"""Advisory reason codes on the wire.

`tests/unit/engine/test_advisory_reasons.py` covers what the engine emits. This covers
what survives the schema boundary, storage and the analysis cache — the whole point being
that a *client* can read it.

The additive promise is asserted directly: `reasons` defaults to `[]`, so a client
written against the previous contract still validates against every response, and a
stored run written before this field existed still loads.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.engine import composite
from app.engine.reasons import (
    WATER_IRRIGATION_DEFICIT,
    WEATHER_COLD_THRESHOLD_EXCEEDED,
    WEATHER_HEAT_THRESHOLD_EXCEEDED,
)
from app.engine.water import WaterAssessment
from app.engine.weather import WeatherAssessment
from app.schemas.advisory import Advisory
from app.schemas.common import RiskLevel
from app.services.analysis_service import _advisories

APPROVED_KEYS = {
    WATER_IRRIGATION_DEFICIT,
    WEATHER_HEAT_THRESHOLD_EXCEEDED,
    WEATHER_COLD_THRESHOLD_EXCEEDED,
}


def published(drafts) -> list[dict]:
    """Drafts through the real service mapper, as JSON."""
    items = _advisories(uuid4(), uuid4(), datetime.now(UTC), drafts)
    return [item.model_dump(mode="json") for item in items]


def thirsty() -> WaterAssessment:
    return WaterAssessment(
        sufficient=True,
        score=78.0,
        level=RiskLevel.high,
        applied_irrigation_mm=84.0,
        depletion_mm=76.0,
        taw_mm=120.0,
        raw_mm=66.0,
        application_efficiency=0.9,
        parameters_source="crop",
    )


def freezing() -> WeatherAssessment:
    return WeatherAssessment(
        score=55.0,
        level=RiskLevel.moderate,
        frost_risk_days=2,
        cold_threshold_c=4.0,
        thresholds_source="crop",
    )


# --------------------------------------------------------------------------
# The evidence crosses the boundary
# --------------------------------------------------------------------------


def test_the_irrigation_evidence_reaches_the_payload() -> None:
    """None of these six values reaches any other published field — that is why the
    code exists."""
    item = published([composite._irrigation_advisory(thirsty())])[0]

    assert item["reasons"], "the advisory must publish its evidence"
    reason = item["reasons"][0]
    assert reason["key"] == WATER_IRRIGATION_DEFICIT
    assert reason["params"]["applied_irrigation_mm"] == 84.0
    assert reason["params"]["depletion_mm"] == 76.0
    assert reason["params"]["taw_mm"] == 120.0
    assert reason["params"]["raw_mm"] == 66.0
    assert reason["params"]["application_efficiency"] == 0.9
    assert reason["params"]["parameters_source"] == "crop"


def test_the_cold_evidence_reaches_the_payload() -> None:
    item = published([composite._frost_advisory(freezing())])[0]

    reason = item["reasons"][0]
    assert reason["key"] == WEATHER_COLD_THRESHOLD_EXCEEDED
    assert reason["params"] == {
        "frost_risk_days": 2,
        "cold_threshold_c": 4.0,
        "thresholds_source": "crop",
    }


def test_a_rainfed_farm_publishes_no_efficiency_key() -> None:
    """Omitted, not null — consistent with how the disease codes handle an absent bound."""
    water = WaterAssessment(
        sufficient=True,
        score=78.0,
        level=RiskLevel.high,
        applied_irrigation_mm=84.0,
        depletion_mm=76.0,
        taw_mm=120.0,
        raw_mm=66.0,
        application_efficiency=None,
        parameters_source="crop",
    )

    reason = published([composite._irrigation_advisory(water)])[0]["reasons"][0]

    assert "application_efficiency" not in reason["params"]


def test_published_params_are_scalars() -> None:
    for draft in (composite._irrigation_advisory(thirsty()), composite._frost_advisory(freezing())):
        for reason in published([draft])[0]["reasons"]:
            for name, value in reason["params"].items():
                assert isinstance(value, float | int | str), f"{name}={value!r}"


# --------------------------------------------------------------------------
# The prose is untouched
# --------------------------------------------------------------------------


def test_the_published_prose_is_unchanged() -> None:
    item = published([composite._irrigation_advisory(thirsty())])[0]

    assert item["title"] == "Apply about 84 mm of irrigation"
    assert item["rationale"] == (
        "76 mm depleted from a 120 mm root zone, past the 66 mm readily-available threshold."
    )
    assert item["category"] == "irrigation"
    assert item["action_window"] == "within 48 hours"
    assert item["confidence"] == 0.75


def test_the_reason_agrees_with_the_prose_it_sits_beside() -> None:
    """Two forms of one fact. If they disagree, the reason is wrong."""
    item = published([composite._irrigation_advisory(thirsty())])[0]
    params = item["reasons"][0]["params"]

    assert f"{params['applied_irrigation_mm']:.0f} mm of irrigation" in item["title"]
    assert f"{params['depletion_mm']:.0f} mm depleted" in item["rationale"]
    assert f"{params['taw_mm']:.0f} mm root zone" in item["rationale"]


# --------------------------------------------------------------------------
# Backward compatibility
# --------------------------------------------------------------------------


def test_a_payload_without_reasons_still_validates() -> None:
    """A client or stored row written before this field existed must still parse."""
    legacy = {
        "id": str(uuid4()),
        "farm_id": str(uuid4()),
        "category": "irrigation",
        "priority": "high",
        "title": "Apply about 84 mm of irrigation",
        "body": "…",
        "rationale": "…",
        "confidence": 0.75,
        "created_at": datetime.now(UTC).isoformat(),
    }

    advisory = Advisory.model_validate(legacy)

    assert advisory.reasons == []


def test_reasons_is_not_a_required_field() -> None:
    assert Advisory.model_fields["reasons"].is_required() is False


# --------------------------------------------------------------------------
# End to end, persistence and caching
# --------------------------------------------------------------------------


@pytest.fixture
async def analysed(client: AsyncClient, api_prefix: str, farm: dict) -> dict:
    crops = (await client.get(f"{api_prefix}/reference/crops")).json()["items"]
    await client.post(
        f"{api_prefix}/farms/{farm['id']}/crops",
        json={"crop_id": crops[0]["id"], "growth_stage": "flowering", "is_primary": True},
    )
    resp = await client.post(f"{api_prefix}/farms/{farm['id']}/analysis")
    assert resp.status_code == 200, resp.text
    return farm


async def test_every_published_advisory_is_well_formed(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """The simulated environment may fire any subset, so this asserts shape and
    vocabulary rather than that a particular advisory appeared."""
    run = (await client.get(f"{api_prefix}/farms/{analysed['id']}/analysis/latest")).json()

    assert run["advisories"], "an analysis is expected to produce at least one advisory"
    for item in run["advisories"]:
        assert isinstance(item["reasons"], list)
        for reason in item["reasons"]:
            assert reason["key"] in APPROVED_KEYS, f"unapproved key {reason['key']}"
            assert isinstance(reason["params"], dict)


async def test_disease_and_soil_advisories_publish_no_reasons(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """Their evidence is on `disease_risk` and `soil_assessment`. Publishing it twice
    would give the two copies a chance to disagree."""
    run = (await client.get(f"{api_prefix}/farms/{analysed['id']}/analysis/latest")).json()

    for item in run["advisories"]:
        if item["category"] in {"disease", "soil", "planting"}:
            assert item["reasons"] == [], f"{item['category']} must not duplicate evidence"


async def test_a_cached_run_returns_the_same_reasons(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """A cache hit serves the stored payload verbatim."""
    first = (await client.post(f"{api_prefix}/farms/{analysed['id']}/analysis")).json()
    second = (await client.post(f"{api_prefix}/farms/{analysed['id']}/analysis")).json()

    assert second["id"] == first["id"], "expected a cache hit"
    assert second["advisories"] == first["advisories"]


async def test_reasons_survive_a_restart(sqlite_db, client: AsyncClient, api_prefix: str) -> None:
    """Stored inside `analysis_runs.result`, so the field round-trips through JSON."""
    from app.db.memory import store
    from app.db.seed import seed_crops

    seed_crops()
    created = (
        await client.post(
            f"{api_prefix}/farms",
            json={"name": "AdvReasons", "latitude": -1.29, "longitude": 36.82},
        )
    ).json()
    crops = (await client.get(f"{api_prefix}/reference/crops")).json()["items"]
    await client.post(
        f"{api_prefix}/farms/{created['id']}/crops",
        json={"crop_id": crops[0]["id"], "growth_stage": "flowering", "is_primary": True},
    )
    before = (await client.post(f"{api_prefix}/farms/{created['id']}/analysis")).json()

    store.reset()

    after = (await client.get(f"{api_prefix}/farms/{created['id']}/analysis/latest")).json()

    assert after["advisories"] == before["advisories"]


async def test_the_advisories_endpoint_carries_reasons(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """`GET /advisories` is a projection of the stored run and must not narrow it."""
    body = (await client.get(f"{api_prefix}/farms/{analysed['id']}/advisories")).json()

    for item in body["items"]:
        assert "reasons" in item
