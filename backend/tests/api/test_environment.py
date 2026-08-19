"""Environmental endpoints, and the honesty guarantees around them.

The most important assertions in this file are the negative ones: no environmental
payload may ever claim `live` or `cached` in Phase 3. That is the product rule from
`docs/ARCHITECTURE.md` §3, enforced here rather than left to reviewer discipline.
"""

import pytest
from httpx import AsyncClient

ENVIRONMENT_ENDPOINTS = ["weather", "soil", "vegetation"]


@pytest.mark.parametrize("endpoint", ENVIRONMENT_ENDPOINTS)
async def test_environment_payloads_are_marked_simulated(
    client: AsyncClient, api_prefix: str, farm: dict, endpoint: str
) -> None:
    resp = await client.get(f"{api_prefix}/farms/{farm['id']}/{endpoint}")

    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert meta["mode"] == "simulated"
    assert meta["source"] == "simulated"
    assert meta["fetched_at"]
    # The note must say plainly that this is not an observation.
    assert meta["note"]


@pytest.mark.parametrize("endpoint", ENVIRONMENT_ENDPOINTS)
async def test_environment_never_claims_real_data(
    client: AsyncClient, api_prefix: str, farm: dict, endpoint: str
) -> None:
    """Phase 3 has no provider, so `live` and `cached` must be unreachable."""
    resp = await client.get(f"{api_prefix}/farms/{farm['id']}/{endpoint}")

    assert resp.json()["meta"]["mode"] not in {"live", "cached"}


async def test_weather_bundle_has_all_three_horizons(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    """The dashboard needs current, forecast and history in one request."""
    resp = await client.get(f"{api_prefix}/farms/{farm['id']}/weather")

    body = resp.json()
    assert body["current"]["temperature_c"] is not None
    assert len(body["daily"]) == 7
    assert body["hourly"], "hourly steps drive the disease rules"
    assert body["history"]["window_days"] == 30
    assert body["history"]["total_precipitation_mm"] >= 0
    assert body["history"]["rain_days"] >= 0


async def test_weather_respects_window_parameters(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    resp = await client.get(
        f"{api_prefix}/farms/{farm['id']}/weather",
        params={"forecast_days": 14, "history_days": 60},
    )

    body = resp.json()
    assert len(body["daily"]) == 14
    assert body["history"]["window_days"] == 60


async def test_hourly_steps_are_ordered_and_dense(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    resp = await client.get(f"{api_prefix}/farms/{farm['id']}/weather")
    hourly = resp.json()["hourly"]

    times = [h["time"] for h in hourly]
    assert times == sorted(times)
    assert len(hourly) % 24 == 0


async def test_soil_fractions_sum_to_100(client: AsyncClient, api_prefix: str, farm: dict) -> None:
    """Particle-size fractions must be internally consistent, or the texture class
    and every downstream water calculation are meaningless."""
    body = (await client.get(f"{api_prefix}/farms/{farm['id']}/soil")).json()

    total = body["sand_pct"] + body["silt_pct"] + body["clay_pct"]
    assert 99.0 <= total <= 101.0
    assert 0 <= body["ph"] <= 14
    assert body["texture_class"]
    assert body["water_holding_capacity_mm"] > 0


async def test_vegetation_series_is_ordered_with_a_trend(
    client: AsyncClient, api_prefix: str, farm: dict
) -> None:
    body = (await client.get(f"{api_prefix}/farms/{farm['id']}/vegetation")).json()

    dates = [p["date"] for p in body["series"]]
    assert dates == sorted(dates)
    assert all(-1 <= p["ndvi"] <= 1 for p in body["series"])
    assert body["trend"] in {"improving", "stable", "declining"}
    assert body["current_ndvi"] == body["series"][-1]["ndvi"]


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ENVIRONMENT_ENDPOINTS)
async def test_repeated_requests_return_identical_values(
    client: AsyncClient, api_prefix: str, farm: dict, endpoint: str
) -> None:
    """Identical requests must produce identical results.

    `fetched_at` is excluded because it records when the payload was produced, which
    genuinely differs between calls.
    """
    first = (await client.get(f"{api_prefix}/farms/{farm['id']}/{endpoint}")).json()
    second = (await client.get(f"{api_prefix}/farms/{farm['id']}/{endpoint}")).json()

    first.pop("meta"), second.pop("meta")
    if "current" in first:  # observed_at moves with the clock
        first["current"].pop("observed_at"), second["current"].pop("observed_at")

    assert first == second


async def test_different_locations_produce_different_soil(
    client: AsyncClient, api_prefix: str
) -> None:
    """Determinism must not collapse into every farm looking the same."""
    nairobi = await client.post(
        f"{api_prefix}/farms", json={"name": "A", "latitude": -1.29, "longitude": 36.82}
    )
    iowa = await client.post(
        f"{api_prefix}/farms", json={"name": "B", "latitude": 42.03, "longitude": -93.63}
    )

    a = (await client.get(f"{api_prefix}/farms/{nairobi.json()['id']}/soil")).json()
    b = (await client.get(f"{api_prefix}/farms/{iowa.json()['id']}/soil")).json()

    assert (a["ph"], a["sand_pct"]) != (b["ph"], b["sand_pct"])


async def test_latitude_drives_a_plausible_temperature_gradient(
    client: AsyncClient, api_prefix: str
) -> None:
    """A tropical site should read warmer than a high-latitude one — the simulation
    is shaped by climatology, not pure noise."""
    tropical = await client.post(
        f"{api_prefix}/farms", json={"name": "Tropics", "latitude": 0.0, "longitude": 36.0}
    )
    polar = await client.post(
        f"{api_prefix}/farms", json={"name": "North", "latitude": 64.0, "longitude": 20.0}
    )

    warm = (await client.get(f"{api_prefix}/farms/{tropical.json()['id']}/weather")).json()
    cold = (await client.get(f"{api_prefix}/farms/{polar.json()['id']}/weather")).json()

    assert warm["history"]["mean_temp_c"] > cold["history"]["mean_temp_c"]
