"""Persisted analysis runs.

Mirrors `farm_repo`: takes and returns validated Pydantic models, never ORM objects, so
a detached instance can never reach the service layer. The whole payload round-trips
through the `result` JSON column, which is what lets a stored run be served byte-for-byte
as it was computed.

**Ownership is resolved by the caller, not here.** Every read takes an optional
`user_id` and, when given, a run belonging to anyone else is invisible — the same
discipline `farm_repo.get_farm` uses, so a caller cannot tell "not yours" from "does not
exist".

**Runs are immutable.** Nothing in this module updates a row. A re-analysis inserts a
new one, which is what makes `created_at DESC` a reliable "latest" and what makes a
stored run safe to hand out without copying.
"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.db.session import session_scope
from app.models import AnalysisRunORM
from app.schemas.analysis import AnalysisRun


def _as_run(row: AnalysisRunORM) -> AnalysisRun:
    """Rebuild the published model from the stored payload.

    Validated rather than trusted: the row was written by an older version of this code
    in the general case, so a payload that no longer satisfies the schema should fail
    loudly here rather than reaching a response half-formed.
    """
    return AnalysisRun.model_validate(row.result)


def insert_run(
    run: AnalysisRun,
    *,
    inputs_hash: str,
    engine_version: str,
    ruleset_version: str,
    user_id: UUID | None = None,
) -> None:
    """Store one completed run.

    The reproducibility columns are passed in rather than read off the run, because
    `AnalysisRun` is published in the frozen contract and has no field for any of them.
    They exist only to answer whether a stored result can be reused.
    """
    with session_scope() as db:
        db.add(
            AnalysisRunORM(
                id=run.id,
                farm_id=run.farm_id,
                user_id=user_id,
                created_at=run.created_at,
                duration_ms=run.duration_ms,
                status=str(run.status),
                overall_health_score=run.overall_health_score,
                overall_band=str(run.overall_band),
                ai_mode=str(run.ai_mode),
                inputs_hash=inputs_hash,
                engine_version=engine_version,
                ruleset_version=ruleset_version,
                prompt_version=run.prompt_version,
                model=run.model,
                # `mode="json"` so datetimes and enums serialise to the primitives the
                # JSON column can hold, and so the payload round-trips through
                # `model_validate` unchanged.
                result=run.model_dump(mode="json"),
                degraded_sources=list(run.degraded_sources),
                error=None,
            )
        )


def latest_run(farm_id: UUID, user_id: UUID | None = None) -> AnalysisRun | None:
    """The newest run for a farm, or None."""
    statement = (
        select(AnalysisRunORM)
        .where(AnalysisRunORM.farm_id == farm_id)
        .order_by(AnalysisRunORM.created_at.desc(), AnalysisRunORM.id.desc())
        .limit(1)
    )
    if user_id is not None:
        statement = statement.where(AnalysisRunORM.user_id == user_id)

    with session_scope() as db:
        row = db.scalars(statement).first()
        return _as_run(row) if row is not None else None


def runs_for_farm(farm_id: UUID, user_id: UUID | None = None) -> list[AnalysisRun]:
    """Every run for a farm, newest first — the order the history endpoint reports."""
    statement = (
        select(AnalysisRunORM)
        .where(AnalysisRunORM.farm_id == farm_id)
        .order_by(AnalysisRunORM.created_at.desc(), AnalysisRunORM.id.desc())
    )
    if user_id is not None:
        statement = statement.where(AnalysisRunORM.user_id == user_id)

    with session_scope() as db:
        return [_as_run(row) for row in db.scalars(statement)]


def get_run(run_id: UUID) -> AnalysisRun | None:
    """One run by its own id.

    Deliberately **not** ownership-scoped: the caller resolves ownership through the
    run's farm, because a run whose owner column is null — written before ownership
    existed, or on the in-memory path — must still be reachable by the farm's owner.
    Filtering on `user_id` here would hide those rows from the very person entitled to
    them.
    """
    with session_scope() as db:
        row = db.get(AnalysisRunORM, run_id)
        return _as_run(row) if row is not None else None


def find_cached_run(
    farm_id: UUID,
    inputs_hash: str,
    *,
    engine_version: str,
    ruleset_version: str,
    not_before: datetime | None = None,
    user_id: UUID | None = None,
) -> AnalysisRun | None:
    """A previous run of this exact analysis, if one is still usable.

    Four things must match, and each rules out a different way a stored answer could be
    stale:

    * **farm** — the payload carries `farm_id` on the run and on every advisory, so a
      run computed for another farm is the wrong data even when the inputs were
      identical. This is why the lookup is farm-scoped while the hash is not.
    * **inputs_hash** — the same question, including the provenance of every input, so
      a run computed while a provider was down is not served after it recovers.
    * **engine_version / ruleset_version** — the same code and the same agronomy would
      answer it. A ruleset edit changes its content hash, so a threshold cannot be
      changed without invalidating the runs it produced.
    * **not_before** — the answer is recent enough to still describe the weather.
    """
    statement = (
        select(AnalysisRunORM)
        .where(
            AnalysisRunORM.farm_id == farm_id,
            AnalysisRunORM.inputs_hash == inputs_hash,
            AnalysisRunORM.engine_version == engine_version,
            AnalysisRunORM.ruleset_version == ruleset_version,
        )
        .order_by(AnalysisRunORM.created_at.desc(), AnalysisRunORM.id.desc())
        .limit(1)
    )
    if not_before is not None:
        statement = statement.where(AnalysisRunORM.created_at >= not_before)
    if user_id is not None:
        statement = statement.where(AnalysisRunORM.user_id == user_id)

    with session_scope() as db:
        row = db.scalars(statement).first()
        return _as_run(row) if row is not None else None


def cache_cutoff(now: datetime, ttl_seconds: int) -> datetime | None:
    """The oldest `created_at` a cached run may carry, or None for no expiry."""
    if ttl_seconds <= 0:
        return None
    return now - timedelta(seconds=ttl_seconds)


def count_runs(farm_id: UUID) -> int:
    with session_scope() as db:
        return len(
            db.scalars(select(AnalysisRunORM.id).where(AnalysisRunORM.farm_id == farm_id)).all()
        )
