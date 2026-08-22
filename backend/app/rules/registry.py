"""Load, validate and version the agronomy rulesets.

Three responsibilities:

* **Load** the YAML files in this package, once, and cache them.
* **Validate** them at load time rather than at use time. A malformed threshold should
  fail loudly on the first call, not silently produce a plausible-looking score three
  layers down.
* **Version** them by content hash, so a stored analysis run always names the exact
  ruleset that produced it and an edited threshold cannot go unrecorded.

This module performs file I/O and therefore **does not belong to the engine**. The
engine receives already-resolved :class:`~app.engine.context.CropParameters`; the
service-layer adapter is what calls in here. That split is what keeps
`tests/unit/engine/test_engine_is_pure.py` passing.
"""

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.engine.context import DiseaseRule, Range, RuleCondition
from app.schemas.enums import GrowthStage

RULES_DIR = Path(__file__).resolve().parent

CROP_COEFFICIENTS_FILE = "crop_coefficients.yaml"

#: Coefficient keys every crop, category default and global default must define.
_COEFFICIENT_KEYS = ("kc_ini", "kc_mid", "kc_end", "root_depth_m", "depletion_fraction")

#: Physically plausible bounds. These are sanity rails, not agronomy: a Kc of 4.0 or a
#: root depth of 40 m is a typo, and catching it here beats propagating it into a water
#: balance that then reports a fictional drought.
_BOUNDS: dict[str, tuple[float, float]] = {
    "kc_ini": (0.0, 1.5),
    "kc_mid": (0.0, 1.5),
    "kc_end": (0.0, 1.5),
    "root_depth_m": (0.05, 3.0),
    "depletion_fraction": (0.05, 0.95),
}

_VALID_BASES = {"none", "ini", "ini_to_mid", "mid", "mid_to_end", "end"}


class RulesetError(RuntimeError):
    """A ruleset file is missing, malformed, or internally inconsistent.

    Raised at load time. This is a deployment fault, not a request fault — it means the
    shipped data is wrong, so it must not be caught and degraded around.
    """


# --------------------------------------------------------------------------
# Versioning
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def ruleset_version() -> str:
    """SHA-256 over every ruleset file in this package, as a 16-character prefix.

    Content-addressed on purpose: a hand-edited threshold changes the version whether
    or not anyone remembered to bump it. Files are hashed in name order with their
    names included, so adding a file changes the digest even if it is empty.
    """
    digest = hashlib.sha256()
    for path in sorted(RULES_DIR.glob("*.yaml")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


RULESET_VERSION = ruleset_version()
"""Stamped onto every analysis run alongside `ENGINE_VERSION`."""


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _read_yaml(filename: str) -> dict[str, Any]:
    path = RULES_DIR / filename
    if not path.is_file():
        raise RulesetError(f"ruleset file missing: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - malformed file is a deploy fault
        raise RulesetError(f"{filename} is not valid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise RulesetError(f"{filename} must contain a mapping at the top level")
    return loaded


def _validate_coefficients(where: str, values: Any) -> dict[str, float]:
    if not isinstance(values, dict):
        raise RulesetError(f"{where}: expected a mapping of coefficients")

    missing = [key for key in _COEFFICIENT_KEYS if key not in values]
    if missing:
        raise RulesetError(f"{where}: missing {', '.join(missing)}")

    resolved: dict[str, float] = {}
    for key in _COEFFICIENT_KEYS:
        raw = values[key]
        if not isinstance(raw, int | float) or isinstance(raw, bool):
            raise RulesetError(f"{where}.{key}: expected a number, got {raw!r}")
        low, high = _BOUNDS[key]
        if not low <= float(raw) <= high:
            raise RulesetError(f"{where}.{key}: {raw} is outside the plausible range {low}-{high}")
        resolved[key] = float(raw)

    if resolved["kc_mid"] < resolved["kc_ini"]:
        # Not a hard physical law, but every crop in FAO-56 Table 12 satisfies it, and
        # a violation almost always means two values were transposed.
        raise RulesetError(f"{where}: kc_mid ({resolved['kc_mid']}) is below kc_ini")

    return resolved


def _validate_stage_curve(curve: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(curve, dict):
        raise RulesetError("stage_curve: expected a mapping")

    known = {stage.value for stage in GrowthStage}
    missing = sorted(known - set(curve))
    if missing:
        raise RulesetError(f"stage_curve: no entry for {', '.join(missing)}")
    unknown = sorted(set(curve) - known)
    if unknown:
        raise RulesetError(f"stage_curve: unknown growth stage(s) {', '.join(unknown)}")

    validated: dict[str, dict[str, Any]] = {}
    for stage, spec in curve.items():
        if not isinstance(spec, dict) or "basis" not in spec:
            raise RulesetError(f"stage_curve.{stage}: expected a mapping with a 'basis'")
        basis = spec["basis"]
        if basis not in _VALID_BASES:
            raise RulesetError(
                f"stage_curve.{stage}.basis: {basis!r} is not one of {sorted(_VALID_BASES)}"
            )
        if basis in {"ini_to_mid", "mid_to_end"}:
            fraction = spec.get("fraction")
            if not isinstance(fraction, int | float) or not 0.0 <= float(fraction) <= 1.0:
                raise RulesetError(
                    f"stage_curve.{stage}.fraction: expected a number in 0-1, got {fraction!r}"
                )
        validated[stage] = dict(spec)
    return validated


@lru_cache(maxsize=1)
def crop_coefficients() -> dict[str, Any]:
    """The validated crop-coefficient ruleset.

    Cached — the files do not change while the process runs. Tests that write a
    temporary ruleset call :func:`reset_cache` first.
    """
    raw = _read_yaml(CROP_COEFFICIENTS_FILE)

    version = raw.get("version")
    if not isinstance(version, int):
        raise RulesetError(f"{CROP_COEFFICIENTS_FILE}: 'version' must be an integer")

    stage_curve = _validate_stage_curve(raw.get("stage_curve"))

    defaults = raw.get("defaults")
    if not isinstance(defaults, dict):
        raise RulesetError(f"{CROP_COEFFICIENTS_FILE}: 'defaults' must be a mapping")

    global_default = _validate_coefficients("defaults.global", defaults.get("global"))

    by_category_raw = defaults.get("by_category") or {}
    if not isinstance(by_category_raw, dict):
        raise RulesetError("defaults.by_category: expected a mapping")
    by_category = {
        name: _validate_coefficients(f"defaults.by_category.{name}", spec)
        for name, spec in by_category_raw.items()
    }

    crops_raw = raw.get("crops") or {}
    if not isinstance(crops_raw, dict):
        raise RulesetError(f"{CROP_COEFFICIENTS_FILE}: 'crops' must be a mapping")
    crops = {
        code: _validate_coefficients(f"crops.{code}", spec) for code, spec in crops_raw.items()
    }

    return {
        "version": version,
        "stage_curve": stage_curve,
        "global": global_default,
        "by_category": by_category,
        "crops": crops,
    }


def reset_cache() -> None:
    """Drop cached rulesets. For tests that swap files on disk."""
    crop_coefficients.cache_clear()
    disease_rules.cache_clear()
    ruleset_version.cache_clear()


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def _kc_at(stage_spec: dict[str, Any], coefficients: dict[str, float]) -> float:
    basis = stage_spec["basis"]
    if basis == "none":
        return 0.0
    if basis == "ini":
        return coefficients["kc_ini"]
    if basis == "mid":
        return coefficients["kc_mid"]
    if basis == "end":
        return coefficients["kc_end"]

    fraction = float(stage_spec["fraction"])
    if basis == "ini_to_mid":
        start, finish = coefficients["kc_ini"], coefficients["kc_mid"]
    else:  # mid_to_end
        start, finish = coefficients["kc_mid"], coefficients["kc_end"]
    return start + (finish - start) * fraction


def kc_by_stage(coefficients: dict[str, float]) -> dict[str, float]:
    """Expand three FAO-56 coefficients into one value per growth stage."""
    curve = crop_coefficients()["stage_curve"]
    return {stage: round(_kc_at(spec, coefficients), 3) for stage, spec in curve.items()}


def resolve_crop_parameters(code: str | None, category: str | None = None) -> dict[str, Any]:
    """FAO-56 parameters for a crop, falling back by category and then globally.

    Returns a mapping with `kc_by_stage`, `root_depth_m`, `depletion_fraction` and
    `parameters_source`. The last is the honest part: a caller can tell whether it got
    the crop's own published coefficients or a category approximation, and say so in
    the factor explanation rather than implying a precision it does not have.
    """
    ruleset = crop_coefficients()

    if code and code in ruleset["crops"]:
        coefficients, source = ruleset["crops"][code], "crop"
    elif category and category in ruleset["by_category"]:
        coefficients, source = ruleset["by_category"][category], "category_default"
    else:
        coefficients, source = ruleset["global"], "global_default"

    return {
        "kc_by_stage": kc_by_stage(coefficients),
        "root_depth_m": coefficients["root_depth_m"],
        "depletion_fraction": coefficients["depletion_fraction"],
        "parameters_source": source,
    }


# --------------------------------------------------------------------------
# Disease rules
# --------------------------------------------------------------------------

DISEASES_FILE = "diseases.yaml"

_CONDITION_TYPES = {"consecutive_hours", "total_hours", "growth_stage_at_least"}

#: Measurement windows a clause may constrain, and the key each is read from.
_RANGE_KEYS = ("temp_c", "humidity_pct", "precipitation_mm")


def _parse_range(where: str, spec: Any) -> Range | None:
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise RulesetError(f"{where}: expected a mapping with 'min' and/or 'max'")

    bounds: dict[str, float | None] = {}
    for source, target in (("min", "low"), ("max", "high")):
        raw = spec.get(source)
        if raw is None:
            bounds[target] = None
            continue
        if not isinstance(raw, int | float) or isinstance(raw, bool):
            raise RulesetError(f"{where}.{source}: expected a number, got {raw!r}")
        bounds[target] = float(raw)

    if bounds["low"] is None and bounds["high"] is None:
        raise RulesetError(f"{where}: needs at least one of 'min' or 'max'")
    if bounds["low"] is not None and bounds["high"] is not None and bounds["low"] > bounds["high"]:
        raise RulesetError(f"{where}: min {bounds['low']} exceeds max {bounds['high']}")

    return Range(low=bounds["low"], high=bounds["high"])


def _parse_condition(where: str, spec: Any) -> RuleCondition:
    if not isinstance(spec, dict):
        raise RulesetError(f"{where}: expected a mapping")

    kind = spec.get("type")
    if kind not in _CONDITION_TYPES:
        raise RulesetError(f"{where}.type: {kind!r} is not one of {sorted(_CONDITION_TYPES)}")

    if kind == "growth_stage_at_least":
        stage = spec.get("stage")
        if stage not in {member.value for member in GrowthStage}:
            raise RulesetError(f"{where}.stage: {stage!r} is not a growth stage")
        return RuleCondition(type=kind, stage=stage)

    hours = spec.get("hours")
    if not isinstance(hours, int) or isinstance(hours, bool) or hours < 1:
        raise RulesetError(f"{where}.hours: expected a positive integer, got {hours!r}")

    windows = {key: _parse_range(f"{where}.{key}", spec.get(key)) for key in _RANGE_KEYS}
    if not any(windows.values()):
        raise RulesetError(f"{where}: needs at least one measurement window")

    return RuleCondition(type=kind, hours=hours, **windows)


def _parse_rule(where: str, spec: Any) -> DiseaseRule:
    if not isinstance(spec, dict):
        raise RulesetError(f"{where}: expected a mapping")

    for required in ("id", "name", "crops", "conditions", "severity"):
        if required not in spec:
            raise RulesetError(f"{where}: missing '{required}'")

    crops = spec["crops"]
    if not isinstance(crops, list) or not crops or not all(isinstance(c, str) for c in crops):
        raise RulesetError(f"{where}.crops: expected a non-empty list of crop codes")

    conditions = spec["conditions"]
    if not isinstance(conditions, list) or not conditions:
        raise RulesetError(f"{where}.conditions: expected a non-empty list")

    severity = spec["severity"]
    if not isinstance(severity, dict):
        raise RulesetError(f"{where}.severity: expected a mapping")

    threshold = severity.get("threshold_hours")
    saturation = severity.get("saturation_hours")
    for label, value in (("threshold_hours", threshold), ("saturation_hours", saturation)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise RulesetError(f"{where}.severity.{label}: expected a positive integer")
    if saturation <= threshold:
        # Probability scales between the two; equal bounds would divide by zero, and an
        # inverted pair would make longer exposure look safer.
        raise RulesetError(
            f"{where}.severity: saturation_hours ({saturation}) must exceed "
            f"threshold_hours ({threshold})"
        )

    parsed = tuple(
        _parse_condition(f"{where}.conditions[{index}]", condition)
        for index, condition in enumerate(conditions)
    )

    durations = [c.hours for c in parsed if c.type in {"consecutive_hours", "total_hours"}]
    if not durations:
        raise RulesetError(f"{where}: needs at least one duration clause")
    if threshold != max(durations):
        # Otherwise the rule could fire before, or well after, the hours its own
        # probability curve is anchored on.
        raise RulesetError(
            f"{where}.severity.threshold_hours ({threshold}) must equal the longest "
            f"duration clause ({max(durations)})"
        )

    return DiseaseRule(
        id=str(spec["id"]),
        name=str(spec["name"]),
        pathogen=str(spec["pathogen"]) if spec.get("pathogen") else None,
        crops=tuple(crops),
        conditions=parsed,
        threshold_hours=threshold,
        saturation_hours=saturation,
        preventive_actions=tuple(str(a) for a in spec.get("preventive_actions") or ()),
        scouting_advice=str(spec["scouting_advice"]) if spec.get("scouting_advice") else None,
    )


@lru_cache(maxsize=1)
def disease_rules() -> tuple[DiseaseRule, ...]:
    """Every validated disease rule, in file order."""
    raw = _read_yaml(DISEASES_FILE)

    if not isinstance(raw.get("version"), int):
        raise RulesetError(f"{DISEASES_FILE}: 'version' must be an integer")

    entries = raw.get("rules")
    if not isinstance(entries, list) or not entries:
        raise RulesetError(f"{DISEASES_FILE}: 'rules' must be a non-empty list")

    rules = tuple(_parse_rule(f"rules[{index}]", entry) for index, entry in enumerate(entries))

    identifiers = [rule.id for rule in rules]
    duplicates = {i for i in identifiers if identifiers.count(i) > 1}
    if duplicates:
        raise RulesetError(f"{DISEASES_FILE}: duplicate rule id(s) {sorted(duplicates)}")

    return rules


def disease_rules_for(crop_code: str | None) -> tuple[DiseaseRule, ...]:
    """Rules applicable to one crop.

    Empty for an unplanted farm, which is what stops a pathogen being named for a field
    with nothing in it.
    """
    if crop_code is None:
        return ()
    return tuple(rule for rule in disease_rules() if crop_code in rule.crops)
