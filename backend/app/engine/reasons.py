"""Machine-readable evidence behind a computed finding.

Every engine already explains itself, in English: *"20 consecutive hours at 10-24 °C with
relative humidity at or above 90%"*. That sentence is assembled from numbers the engine
holds and then discards, which leaves a consumer two bad options — parse the prose, or
re-derive the agronomy. A voice assistant that has to answer in Hindi or Arabic can do
neither honestly.

A :class:`Reason` keeps the numbers. `key` names *which* condition was met, from a stable
vocabulary; `params` carries the values that met it. A client maps the key to a phrase in
its own language and interpolates the params, so translation happens over data rather
than over generated sentences.

**Reasons never explain anything the prose does not already say.** They are the same
evidence in a different form — no new threshold, no new calculation, no agronomic
judgement that was not already made. If a reason and its sentence ever disagree, the
reason is wrong.

**Keys are a public vocabulary.** Once a client hard-codes `disease.consecutive_hours_met`
to a translated phrase, renaming it silently breaks that translation in every language.
Treat the constants below the way the frozen contract is treated: add freely, rename
never.

Pure by construction: no provider, no clock, no database, no filesystem — see
`tests/unit/engine/test_engine_is_pure.py`.
"""

from dataclasses import dataclass, field
from typing import Final

#: A reason parameter. Scalars only.
#:
#: Nested objects would push a client back to walking a structure it has to understand,
#: which is the problem this type exists to remove — a template needs values it can drop
#: into a sentence.
ParamValue = float | int | str | None


@dataclass(frozen=True, slots=True)
class Reason:
    """One piece of structured evidence.

    Frozen, so a reason cannot be edited after the engine has stated it.
    """

    key: str
    params: dict[str, ParamValue] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Disease
#
# One key per `RuleCondition.type`. The engine evaluates a rule as a conjunction of
# clauses, so a matched rule yields one reason per clause — which is exactly how the
# prose reads, and keeps the two forms in step.
# --------------------------------------------------------------------------

DISEASE_CONSECUTIVE_HOURS_MET: Final = "disease.consecutive_hours_met"
"""An unbroken run of hours inside the rule's temperature/humidity/rain window."""

DISEASE_TOTAL_HOURS_MET: Final = "disease.total_hours_met"
"""Enough matching hours accumulated, not necessarily consecutive."""

DISEASE_GROWTH_STAGE_MET: Final = "disease.growth_stage_met"
"""The crop had reached the stage the rule gates on."""

#: `RuleCondition.type` → reason key.
#:
#: A mapping rather than a computed string, so an unrecognised clause type produces no
#: reason instead of inventing a key no client can translate.
DISEASE_CONDITION_KEYS: Final[dict[str, str]] = {
    "consecutive_hours": DISEASE_CONSECUTIVE_HOURS_MET,
    "total_hours": DISEASE_TOTAL_HOURS_MET,
    "growth_stage_at_least": DISEASE_GROWTH_STAGE_MET,
}


# --------------------------------------------------------------------------
# Advisories
#
# Only where the evidence is otherwise lost. A disease advisory cites the same rule the
# disease section already publishes under `risks[].reasons`, and a soil advisory cites
# `limitations`, `score` and `band`, all published on `soil_assessment` — so a key for
# either would emit the same facts twice and give them two chances to disagree.
#
# These three cite numbers that reach no published field at all.
# --------------------------------------------------------------------------

WATER_IRRIGATION_DEFICIT: Final = "water.irrigation_deficit"
"""The root zone drew past its readily-available threshold, so a depth was advised.

`water_risk` publishes `deficit_mm` and `recommended_irrigation_mm`, but not the FAO-56
reservoir the advice was computed against — the depletion tracked, the total available
water, the readily-available fraction, or the efficiency the depth was grossed up by.
"""

WEATHER_HEAT_THRESHOLD_EXCEEDED: Final = "weather.heat_threshold_exceeded"
"""Forecast days above the heat threshold the engine actually applied.

`heat_stress_days` is published; the threshold it was counted against is not, so the
count cannot be restated as a temperature in any language without it.
"""

WEATHER_COLD_THRESHOLD_EXCEEDED: Final = "weather.cold_threshold_exceeded"
"""Forecast days below the crop's cold-damage threshold.

Distinct from the heat key even though both advisories carry `category: "weather"` —
which is why a consumer cannot currently tell the two apart.
"""
