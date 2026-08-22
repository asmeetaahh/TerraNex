"""One context per run.

`run_analysis` derives the cache key from an `AnalysisContext`; `_build_run` scores the
run. While each built its own context, the two could describe different analyses — and
they did. The blocks differed by a single guard: `run_analysis` only read the planting's
growth stage when the crop resolved in the catalog, `_build_run` always read it. So a
planting whose crop had gone missing was hashed as `not_planted` and scored as
`flowering`.

That is not a stale answer, it is a mislabelled one. Crop coefficients are indexed by
growth stage, so the digest described a water balance the stored run had not computed —
and because the digest ignored the stage, advancing the crop from flowering to
maturity left it unchanged, and the cache went on serving the flowering run forever.

The fix is structural rather than a second matching guard: the context is built once and
passed down, so there is no second block left to drift. `test_the_scored_context_is_the
_hashed_context` asserts that by identity, which no pair of independently built contexts
can satisfy however carefully they are kept in step.

A crop that fails to resolve is reachable in normal operation — a catalog seeded after
the plantings, a soft-deleted reference row, a store and database that disagree — and
`get_crop` is patched here to produce exactly that state. Only `analysis_service` imports
it lazily, so the patch reaches the analysis path and leaves the farm routes' own
validation untouched, which is what lets the planting be created and edited normally.
"""

import pytest
from httpx import AsyncClient

from app.services import analysis_service


@pytest.fixture
def unresolvable_crop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The planting survives; its catalog entry does not."""
    monkeypatch.setattr("app.services.reference_service.get_crop", lambda _crop_id: None)


@pytest.fixture
def contexts(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Capture the context handed to `_build_run` and the one handed to `_store_run`."""
    seen: dict = {}

    build_run = analysis_service._build_run
    store_run = analysis_service._store_run

    def spy_build_run(record, env, context, crop, stage):
        seen["scored"] = context
        return build_run(record, env, context, crop, stage)

    def spy_store_run(run, context, user):
        seen["hashed"] = context
        return store_run(run, context, user)

    monkeypatch.setattr(analysis_service, "_build_run", spy_build_run)
    monkeypatch.setattr(analysis_service, "_store_run", spy_store_run)
    return seen


async def analyse(client: AsyncClient, api_prefix: str, farm_id: str) -> dict:
    response = await client.post(f"{api_prefix}/farms/{farm_id}/analysis")
    assert response.status_code == 200, response.text
    return response.json()


async def set_stage(client: AsyncClient, api_prefix: str, farm_id: str, stage: str) -> None:
    plantings = (await client.get(f"{api_prefix}/farms/{farm_id}/crops")).json()["items"]
    response = await client.patch(
        f"{api_prefix}/farms/{farm_id}/crops/{plantings[0]['id']}",
        json={"growth_stage": stage},
    )
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------
# The structural guarantee
# --------------------------------------------------------------------------


async def test_build_run_scores_the_context_it_is_given(
    client: AsyncClient, api_prefix: str, planted_farm: dict
) -> None:
    """The load-bearing one: `_build_run` must have no context of its own.

    Two contexts identical but for the growth stage are scored directly. A `_build_run`
    that rebuilt internally would resolve the same planting both times and return the
    same water balance, so the argument is proven to be the thing actually scored — an
    identity assertion on the argument could not show this, because a rebuilt context
    would simply shadow it.

    Germination and mid-season sit at opposite ends of the FAO-56 Kc curve, so crop
    water demand — and therefore the balance — genuinely has to move between them.
    """
    from dataclasses import replace
    from uuid import UUID

    from app.core.deps import demo_user
    from app.schemas.enums import GrowthStage
    from app.services import environment_service
    from app.services.analysis_context import build_context
    from app.services.farm_service import primary_planting, require_farm
    from app.services.reference_service import get_crop

    record = require_farm(UUID(planted_farm["id"]), demo_user())
    env = await environment_service.gather_environment(record)
    planting = primary_planting(record.id)
    crop = get_crop(planting.crop_id)

    early = build_context(record, env, crop, GrowthStage.germination, planting)
    late = replace(early, growth_stage=GrowthStage.flowering.value, crop=early.crop)

    early_run = analysis_service._build_run(record, env, early, crop, GrowthStage.germination)
    late_run = analysis_service._build_run(record, env, late, crop, GrowthStage.flowering)

    assert early.growth_stage != late.growth_stage
    assert early_run.water_risk != late_run.water_risk


async def test_the_scored_context_is_the_hashed_context(
    client: AsyncClient, api_prefix: str, planted_farm: dict, contexts: dict
) -> None:
    """Identity, not equality. The object whose digest labels the stored run is the
    object the run was scored from — nothing equal-but-separate is accepted."""
    await analyse(client, api_prefix, planted_farm["id"])

    assert contexts["scored"] is contexts["hashed"]


async def test_the_scored_context_is_the_hashed_context_when_the_crop_is_missing(
    client: AsyncClient, api_prefix: str, planted_farm: dict, contexts: dict, unresolvable_crop
) -> None:
    """The case that actually diverged."""
    await analyse(client, api_prefix, planted_farm["id"])

    assert contexts["scored"] is contexts["hashed"]


async def test_a_missing_crop_does_not_erase_the_growth_stage(
    client: AsyncClient, api_prefix: str, planted_farm: dict, contexts: dict, unresolvable_crop
) -> None:
    """The planting states the stage, so an unresolvable crop costs the run its
    coefficients — not its calendar. Hashing it as `not_planted` was the defect."""
    await analyse(client, api_prefix, planted_farm["id"])

    assert contexts["hashed"].growth_stage == "flowering"
    assert contexts["hashed"].planting_date is not None


# --------------------------------------------------------------------------
# The consequence a user would have seen
# --------------------------------------------------------------------------


async def test_a_stage_change_invalidates_the_cache_when_the_crop_is_missing(
    client: AsyncClient, api_prefix: str, planted_farm: dict, unresolvable_crop
) -> None:
    """With the stage outside the digest, this returned the flowering run for a farm
    that had reached maturity — indefinitely, since nothing else about the farm moved.
    """
    flowering = await analyse(client, api_prefix, planted_farm["id"])

    await set_stage(client, api_prefix, planted_farm["id"], "maturity")
    matured = await analyse(client, api_prefix, planted_farm["id"])

    assert matured["id"] != flowering["id"]


async def test_the_digest_moves_with_the_growth_stage_when_the_crop_is_missing(
    client: AsyncClient, api_prefix: str, planted_farm: dict, contexts: dict, unresolvable_crop
) -> None:
    """The mechanism under the test above, asserted directly: same farm, same weather,
    different stage — different digest."""
    from app.engine.context import inputs_hash

    await analyse(client, api_prefix, planted_farm["id"])
    flowering = inputs_hash(contexts["hashed"])

    await set_stage(client, api_prefix, planted_farm["id"], "maturity")
    await analyse(client, api_prefix, planted_farm["id"])
    matured = inputs_hash(contexts["hashed"])

    assert flowering != matured


async def test_an_identical_run_still_hits_the_cache_when_the_crop_is_missing(
    client: AsyncClient, api_prefix: str, planted_farm: dict, unresolvable_crop
) -> None:
    """The fix must not turn every unresolvable-crop analysis into a miss."""
    first = await analyse(client, api_prefix, planted_farm["id"])
    second = await analyse(client, api_prefix, planted_farm["id"])

    assert second["id"] == first["id"]
