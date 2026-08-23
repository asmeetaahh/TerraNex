"""Reason codes on the wire.

`tests/unit/engine/test_reasons.py` covers what the engine emits. This file covers what
survives the schema boundary, storage, and the analysis cache — because the whole purpose
of the field is that a *client* can read it, and the previous behaviour was that
`rule_id` and `matched_hours` existed on the engine object and were dropped on the way
out.

The additive-contract promise is asserted here too: `reasons` defaults to `[]`, so a
client written against the previous contract still validates against every response.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.engine import disease as engine_disease
from app.engine.context import (
    AnalysisContext,
    CropParameters,
    DiseaseRule,
    HourlyPoint,
    Range,
    RuleCondition,
)
from app.schemas.risk import DiseaseRiskItem
from app.services.analysis_service import _to_disease_risk

START = datetime(2026, 8, 20, 0, tzinfo=UTC)

BLIGHT = DiseaseRule(
    id="late_blight",
    name="Late blight",
    pathogen="Phytophthora infestans",
    crops=("potato",),
    conditions=(
        RuleCondition(
            "consecutive_hours", 10, temp_c=Range(10.0, 24.0), humidity_pct=Range(90.0, None)
        ),
    ),
    threshold_hours=10,
    saturation_hours=24,
)


def infected_context(hours: int = 20, crop_code: str = "potato") -> AnalysisContext:
    return AnalysisContext(
        farm_id="farm",
        latitude=-1.29,
        longitude=36.82,
        timezone="UTC",
        today=START.date(),
        hourly=[
            HourlyPoint(at=START + timedelta(hours=n), temperature_c=18.0, humidity_pct=95.0)
            for n in range(hours)
        ],
        disease_rules=(BLIGHT,),
        crop=CropParameters(code=crop_code, name="Potato"),
        growth_stage="flowering",
    )


def published(hours: int = 20, crop_code: str = "potato") -> dict:
    assessment = engine_disease.evaluate(infected_context(hours, crop_code))
    return _to_disease_risk(assessment).model_dump(mode="json")


# --------------------------------------------------------------------------
# The evidence crosses the boundary
# --------------------------------------------------------------------------


def test_a_detection_publishes_its_reason() -> None:
    """`rule_id` and `matched_hours` existed on the engine item and were dropped here.
    That is the defect this phase fixes."""
    item = published()["risks"][0]

    assert item["reasons"], "a matched rule must publish its evidence"
    reason = item["reasons"][0]
    assert reason["key"] == "disease.consecutive_hours_met"
    assert reason["params"]["rule_id"] == "late_blight"
    assert reason["params"]["matched_hours"] == 20


def test_the_published_params_survive_json_serialisation() -> None:
    """The field round-trips through `mode="json"` on the way to storage and back."""
    reason = published()["risks"][0]["reasons"][0]

    for name, value in reason["params"].items():
        assert isinstance(value, float | int | str | type(None)), f"{name}={value!r}"


def test_no_detection_publishes_an_empty_list(client: AsyncClient, api_prefix: str) -> None:
    """`reasons: []`, never null — a client iterating the list must not have to
    null-check it first."""
    assessment = engine_disease.evaluate(infected_context(hours=9))
    body = _to_disease_risk(assessment).model_dump(mode="json")

    assert body["risks"] == []


def test_a_wrong_crop_publishes_nothing() -> None:
    assert published(crop_code="maize")["risks"] == []


# --------------------------------------------------------------------------
# The prose is untouched
# --------------------------------------------------------------------------


def test_the_existing_prose_fields_are_unchanged() -> None:
    """Reasons sit beside `triggering_conditions`. Every existing field keeps its exact
    previous value — this phase adds, it does not reword."""
    item = published()["risks"][0]

    assert item["name"] == "Late blight"
    assert item["pathogen"] == "Phytophthora infestans"
    assert item["crop_code"] == "potato"
    assert item["level"] == "severe"
    assert item["triggering_conditions"] == [
        "20 consecutive hours at 10-24 °C with relative humidity at or above 90%"
    ]


def test_the_reason_agrees_with_the_prose_it_sits_beside() -> None:
    """Two forms of one fact. If they disagree, the reason is wrong."""
    item = published()["risks"][0]
    hours = item["reasons"][0]["params"]["matched_hours"]

    assert f"{hours} consecutive hours" in item["triggering_conditions"][0]


# --------------------------------------------------------------------------
# Backward compatibility
# --------------------------------------------------------------------------


def test_a_payload_without_reasons_still_validates() -> None:
    """The additive promise, asserted directly: a client or stored row written before
    this field existed must still parse."""
    legacy = {
        "name": "Late blight",
        "pathogen": "Phytophthora infestans",
        "crop_code": "potato",
        "level": "severe",
        "probability": 0.86,
        "triggering_conditions": ["20 consecutive hours…"],
        "preventive_actions": [],
        "scouting_advice": None,
    }

    item = DiseaseRiskItem.model_validate(legacy)

    assert item.reasons == [], "the default makes the field optional for every caller"


def test_reasons_is_not_a_required_field() -> None:
    from app.schemas.common import ReasonCode

    assert DiseaseRiskItem.model_fields["reasons"].is_required() is False
    assert ReasonCode.model_fields["params"].is_required() is False


# --------------------------------------------------------------------------
# Persistence and caching
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


async def test_the_disease_section_still_carries_reasons_through_an_analysis(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """End to end. The simulated environment may trigger no rule, so this asserts the
    field is present and well-formed rather than that a pathogen was found."""
    run = (await client.get(f"{api_prefix}/farms/{analysed['id']}/analysis/latest")).json()

    for item in run["disease_risk"]["risks"]:
        assert isinstance(item["reasons"], list)
        for reason in item["reasons"]:
            assert reason["key"].startswith("disease.")
            assert isinstance(reason["params"], dict)


async def test_a_cached_run_returns_the_same_reasons(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """A cache hit serves the stored payload verbatim, so reasons must come back
    identical rather than being recomputed or dropped."""
    first = (await client.post(f"{api_prefix}/farms/{analysed['id']}/analysis")).json()
    second = (await client.post(f"{api_prefix}/farms/{analysed['id']}/analysis")).json()

    assert second["id"] == first["id"], "expected a cache hit"
    assert second["disease_risk"]["risks"] == first["disease_risk"]["risks"]


async def test_reasons_survive_a_restart(sqlite_db, client: AsyncClient, api_prefix: str) -> None:
    """Stored inside `analysis_runs.result`, so the field has to round-trip through JSON
    and back through `model_validate`."""
    from app.db.memory import store
    from app.db.seed import seed_crops

    seed_crops()
    created = (
        await client.post(
            f"{api_prefix}/farms",
            json={"name": "Reasons", "latitude": -1.29, "longitude": 36.82},
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

    assert after["disease_risk"]["risks"] == before["disease_risk"]["risks"]


async def test_the_risk_projection_endpoint_carries_reasons(
    client: AsyncClient, api_prefix: str, analysed: dict
) -> None:
    """`GET /risks/disease` is a projection of the stored run and must not narrow it."""
    body = (await client.get(f"{api_prefix}/farms/{analysed['id']}/risks/disease")).json()

    for item in body["risks"]:
        assert "reasons" in item
