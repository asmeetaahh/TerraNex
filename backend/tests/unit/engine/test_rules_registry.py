"""The agronomy ruleset loads, validates, and versions itself.

The registry is the only place a malformed coefficient can be caught cheaply. Once a
bad value reaches the water balance it produces a plausible-looking deficit that no
test downstream can distinguish from a real one, so the validation here is doing real
work rather than ceremony.
"""

import pytest

from app.db.seed import load_crop_catalog
from app.rules import registry
from app.rules.registry import (
    RULESET_VERSION,
    RulesetError,
    crop_coefficients,
    kc_by_stage,
    resolve_crop_parameters,
    ruleset_version,
)
from app.schemas.enums import GrowthStage


@pytest.fixture(autouse=True)
def _clear_cache():
    """The registry caches aggressively; tests that swap files need a clean slate."""
    registry.reset_cache()
    yield
    registry.reset_cache()


# --------------------------------------------------------------------------
# Loading and validation
# --------------------------------------------------------------------------


def test_the_shipped_ruleset_loads() -> None:
    ruleset = crop_coefficients()

    assert ruleset["version"] == 1
    assert ruleset["crops"], "no crops defined"
    assert ruleset["by_category"], "no category defaults defined"


def test_every_growth_stage_has_a_curve_entry() -> None:
    """A stage with no entry would raise mid-analysis rather than at load."""
    curve = crop_coefficients()["stage_curve"]

    assert set(curve) == {stage.value for stage in GrowthStage}


def test_every_catalog_crop_has_coefficients() -> None:
    """Not strictly required — the category fallback covers a gap — but a missing
    entry means a crop silently loses its own coefficients, so it should be a
    deliberate choice rather than an oversight."""
    catalog = {crop.code for crop in load_crop_catalog()}
    defined = set(crop_coefficients()["crops"])

    assert catalog - defined == set(), f"crops with no coefficients: {sorted(catalog - defined)}"


def test_coefficients_are_physically_plausible() -> None:
    ruleset = crop_coefficients()
    everything = [*ruleset["crops"].values(), *ruleset["by_category"].values(), ruleset["global"]]

    for values in everything:
        assert 0.0 <= values["kc_ini"] <= 1.5
        assert 0.0 <= values["kc_mid"] <= 1.5
        assert 0.0 <= values["kc_end"] <= 1.5
        assert 0.05 <= values["root_depth_m"] <= 3.0
        assert 0.05 <= values["depletion_fraction"] <= 0.95


def test_peak_demand_is_never_below_initial_demand() -> None:
    """True of every crop in FAO-56 Table 12. A violation is a transposition."""
    ruleset = crop_coefficients()

    for code, values in ruleset["crops"].items():
        assert values["kc_mid"] >= values["kc_ini"], code


# --------------------------------------------------------------------------
# Validation rejects bad data
# --------------------------------------------------------------------------


def _write_ruleset(tmp_path, body: str, monkeypatch) -> None:
    (tmp_path / "crop_coefficients.yaml").write_text(body, encoding="utf-8")
    monkeypatch.setattr(registry, "RULES_DIR", tmp_path)
    registry.reset_cache()


VALID_CURVE = """
version: 1
stage_curve:
  not_planted: {basis: none}
  germination: {basis: ini}
  seedling: {basis: ini_to_mid, fraction: 0.25}
  vegetative: {basis: ini_to_mid, fraction: 0.7}
  flowering: {basis: mid}
  fruiting: {basis: mid}
  maturity: {basis: mid_to_end, fraction: 0.6}
  harvested: {basis: end}
defaults:
  global: {kc_ini: 0.4, kc_mid: 1.05, kc_end: 0.55, root_depth_m: 1.0, depletion_fraction: 0.5}
"""


def test_a_missing_coefficient_is_rejected(tmp_path, monkeypatch) -> None:
    _write_ruleset(
        tmp_path,
        VALID_CURVE + "crops:\n  maize: {kc_ini: 0.3, kc_mid: 1.2}\n",
        monkeypatch,
    )

    with pytest.raises(RulesetError, match="missing"):
        crop_coefficients()


def test_an_out_of_range_coefficient_is_rejected(tmp_path, monkeypatch) -> None:
    _write_ruleset(
        tmp_path,
        VALID_CURVE + "crops:\n  maize: {kc_ini: 0.3, kc_mid: 9.0, kc_end: 0.35, "
        "root_depth_m: 1.3, depletion_fraction: 0.55}\n",
        monkeypatch,
    )

    with pytest.raises(RulesetError, match="plausible range"):
        crop_coefficients()


def test_a_transposed_coefficient_pair_is_rejected(tmp_path, monkeypatch) -> None:
    _write_ruleset(
        tmp_path,
        VALID_CURVE + "crops:\n  maize: {kc_ini: 1.2, kc_mid: 0.3, kc_end: 0.35, "
        "root_depth_m: 1.3, depletion_fraction: 0.55}\n",
        monkeypatch,
    )

    with pytest.raises(RulesetError, match="below kc_ini"):
        crop_coefficients()


def test_a_missing_growth_stage_is_rejected(tmp_path, monkeypatch) -> None:
    _write_ruleset(
        tmp_path,
        "version: 1\nstage_curve:\n  germination: {basis: ini}\n"
        "defaults:\n  global: {kc_ini: 0.4, kc_mid: 1.05, kc_end: 0.55, "
        "root_depth_m: 1.0, depletion_fraction: 0.5}\n",
        monkeypatch,
    )

    with pytest.raises(RulesetError, match="no entry for"):
        crop_coefficients()


def test_an_unknown_basis_is_rejected(tmp_path, monkeypatch) -> None:
    body = VALID_CURVE.replace("flowering: {basis: mid}", "flowering: {basis: peak}")
    _write_ruleset(tmp_path, body, monkeypatch)

    with pytest.raises(RulesetError, match="not one of"):
        crop_coefficients()


def test_a_missing_file_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(registry, "RULES_DIR", tmp_path)
    registry.reset_cache()

    with pytest.raises(RulesetError, match="missing"):
        crop_coefficients()


# --------------------------------------------------------------------------
# Stage expansion
# --------------------------------------------------------------------------


def test_stage_expansion_covers_every_stage() -> None:
    expanded = kc_by_stage(crop_coefficients()["crops"]["maize"])

    assert set(expanded) == {stage.value for stage in GrowthStage}


def test_an_unplanted_field_has_no_crop_demand() -> None:
    """Kc of zero is the only honest value with nothing in the ground."""
    expanded = kc_by_stage(crop_coefficients()["crops"]["maize"])

    assert expanded[GrowthStage.not_planted] == 0.0


def test_peak_demand_lands_at_flowering() -> None:
    maize = crop_coefficients()["crops"]["maize"]
    expanded = kc_by_stage(maize)

    assert expanded[GrowthStage.flowering] == pytest.approx(maize["kc_mid"])
    assert expanded[GrowthStage.flowering] > expanded[GrowthStage.germination]
    assert expanded[GrowthStage.flowering] > expanded[GrowthStage.harvested]


def test_demand_rises_then_falls_across_the_season() -> None:
    """The FAO-56 curve shape, asserted as a property rather than per-value."""
    expanded = kc_by_stage(crop_coefficients()["crops"]["maize"])
    rising = [
        expanded[GrowthStage.germination],
        expanded[GrowthStage.seedling],
        expanded[GrowthStage.vegetative],
        expanded[GrowthStage.flowering],
    ]
    falling = [
        expanded[GrowthStage.fruiting],
        expanded[GrowthStage.maturity],
        expanded[GrowthStage.harvested],
    ]

    assert rising == sorted(rising)
    assert falling == sorted(falling, reverse=True)


def test_interpolation_sits_between_its_endpoints() -> None:
    values = {"kc_ini": 0.30, "kc_mid": 1.20, "kc_end": 0.35}
    expanded = kc_by_stage({**values, "root_depth_m": 1.0, "depletion_fraction": 0.5})

    assert values["kc_ini"] < expanded[GrowthStage.seedling] < values["kc_mid"]
    assert expanded[GrowthStage.seedling] < expanded[GrowthStage.vegetative]


# --------------------------------------------------------------------------
# Resolution and fallback
# --------------------------------------------------------------------------


def test_a_known_crop_uses_its_own_coefficients() -> None:
    resolved = resolve_crop_parameters("maize", "cereal")

    assert resolved["parameters_source"] == "crop"
    assert resolved["root_depth_m"] == crop_coefficients()["crops"]["maize"]["root_depth_m"]


def test_an_unknown_crop_falls_back_to_its_category() -> None:
    resolved = resolve_crop_parameters("teff", "cereal")

    assert resolved["parameters_source"] == "category_default"
    assert resolved["root_depth_m"] == crop_coefficients()["by_category"]["cereal"]["root_depth_m"]


def test_an_unknown_category_falls_back_globally() -> None:
    resolved = resolve_crop_parameters("teff", "pseudocereal")

    assert resolved["parameters_source"] == "global_default"
    assert resolved["root_depth_m"] == crop_coefficients()["global"]["root_depth_m"]


def test_no_crop_at_all_still_resolves() -> None:
    """A farm with no planting must not crash the engine."""
    resolved = resolve_crop_parameters(None, None)

    assert resolved["parameters_source"] == "global_default"
    assert resolved["kc_by_stage"]


def test_the_fallback_is_reported_not_hidden() -> None:
    """The honesty property: a caller can always tell an approximation from a
    published coefficient, and say so in the factor explanation."""
    sources = {
        resolve_crop_parameters("maize", "cereal")["parameters_source"],
        resolve_crop_parameters("teff", "cereal")["parameters_source"],
        resolve_crop_parameters(None, None)["parameters_source"],
    }

    assert sources == {"crop", "category_default", "global_default"}


# --------------------------------------------------------------------------
# Versioning
# --------------------------------------------------------------------------


def test_the_version_is_a_stable_hash() -> None:
    assert RULESET_VERSION
    assert len(RULESET_VERSION) == 16
    assert ruleset_version() == ruleset_version()


def test_editing_a_rule_changes_the_version(tmp_path, monkeypatch) -> None:
    """The property that makes a stored run traceable: a threshold cannot be edited
    without the recorded version moving."""
    _write_ruleset(tmp_path, VALID_CURVE + "crops: {}\n", monkeypatch)
    before = ruleset_version()

    _write_ruleset(
        tmp_path, VALID_CURVE.replace("kc_mid: 1.05", "kc_mid: 1.10") + "crops: {}\n", monkeypatch
    )
    after = ruleset_version()

    assert before != after
