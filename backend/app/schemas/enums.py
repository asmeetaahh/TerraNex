"""Domain enums and the machine-readable enum catalog.

`GET /api/v1/reference/enums` returns :class:`EnumCatalog`, so the frontend renders
dropdown options and human labels from the API rather than hardcoding them. Adding
an enum member becomes a backend-only change.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.common import DataMode, RiskLevel, ScoreBand


class IrrigationType(StrEnum):
    rainfed = "rainfed"
    drip = "drip"
    sprinkler = "sprinkler"
    flood = "flood"
    furrow = "furrow"
    none = "none"


class FarmingPractice(StrEnum):
    conventional = "conventional"
    organic = "organic"
    regenerative = "regenerative"
    mixed = "mixed"


class CropCategory(StrEnum):
    cereal = "cereal"
    legume = "legume"
    oilseed = "oilseed"
    vegetable = "vegetable"
    fruit = "fruit"
    tuber = "tuber"
    fibre = "fibre"
    forage = "forage"
    other = "other"


class Season(StrEnum):
    spring = "spring"
    summer = "summer"
    autumn = "autumn"
    winter = "winter"
    year_round = "year_round"


class GrowthStage(StrEnum):
    not_planted = "not_planted"
    germination = "germination"
    seedling = "seedling"
    vegetative = "vegetative"
    flowering = "flowering"
    fruiting = "fruiting"
    maturity = "maturity"
    harvested = "harvested"


class CropStatus(StrEnum):
    planned = "planned"
    growing = "growing"
    harvested = "harvested"
    failed = "failed"


class AdvisoryCategory(StrEnum):
    irrigation = "irrigation"
    disease = "disease"
    pest = "pest"
    nutrient = "nutrient"
    weather = "weather"
    planting = "planting"
    harvest = "harvest"
    soil = "soil"
    regenerative = "regenerative"


class AdvisoryPriority(StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class Severity(StrEnum):
    none = "none"
    mild = "mild"
    moderate = "moderate"
    severe = "severe"


class AnalysisStatus(StrEnum):
    complete = "complete"
    """Every input resolved and the AI produced validated output."""

    partial = "partial"
    """Produced, but some provider or the AI degraded. See `degraded_sources`."""

    failed = "failed"
    """Could not produce a usable result."""


class AIMode(StrEnum):
    """Which engine actually produced the narrative in a response.

    Exposed for the same reason as :class:`~app.schemas.common.DataMode`: a consumer
    must always be able to tell real model output from a canned or fallback response.
    """

    gemini = "gemini"
    mock = "mock"
    fallback = "fallback"
    """Deterministic template text, used when the AI was unavailable or invalid."""


class ImageAnalysisStatus(StrEnum):
    pending = "pending"
    analyzing = "analyzing"
    complete = "complete"
    failed = "failed"


class SoilTexture(StrEnum):
    sand = "sand"
    loamy_sand = "loamy_sand"
    sandy_loam = "sandy_loam"
    loam = "loam"
    silt_loam = "silt_loam"
    silt = "silt"
    sandy_clay_loam = "sandy_clay_loam"
    clay_loam = "clay_loam"
    silty_clay_loam = "silty_clay_loam"
    sandy_clay = "sandy_clay"
    silty_clay = "silty_clay"
    clay = "clay"


class DroughtTolerance(StrEnum):
    low = "low"
    moderate = "moderate"
    high = "high"


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


class EnumValue(BaseModel):
    """One selectable option, with display text supplied by the backend."""

    value: str
    label: str
    description: str | None = None


class EnumCatalog(BaseModel):
    """Every enum the frontend needs to render, with labels.

    Typed per-field (rather than a bare dict) so `openapi-typescript` produces a
    real interface instead of `Record<string, unknown>`.
    """

    irrigation_type: list[EnumValue]
    farming_practice: list[EnumValue]
    crop_category: list[EnumValue]
    season: list[EnumValue]
    growth_stage: list[EnumValue]
    crop_status: list[EnumValue]
    advisory_category: list[EnumValue]
    advisory_priority: list[EnumValue]
    severity: list[EnumValue]
    analysis_status: list[EnumValue]
    ai_mode: list[EnumValue]
    image_analysis_status: list[EnumValue]
    soil_texture: list[EnumValue]
    drought_tolerance: list[EnumValue]
    risk_level: list[EnumValue]
    score_band: list[EnumValue]
    data_mode: list[EnumValue]


def _humanize(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _options(enum_cls: type[StrEnum]) -> list[EnumValue]:
    """Build catalog entries, using each member's docstring as its description."""
    return [EnumValue(value=member.value, label=_humanize(member.value)) for member in enum_cls]


def build_enum_catalog() -> EnumCatalog:
    """Assemble the catalog from the enum definitions above — single source of truth."""
    return EnumCatalog(
        irrigation_type=_options(IrrigationType),
        farming_practice=_options(FarmingPractice),
        crop_category=_options(CropCategory),
        season=_options(Season),
        growth_stage=_options(GrowthStage),
        crop_status=_options(CropStatus),
        advisory_category=_options(AdvisoryCategory),
        advisory_priority=_options(AdvisoryPriority),
        severity=_options(Severity),
        analysis_status=_options(AnalysisStatus),
        ai_mode=_options(AIMode),
        image_analysis_status=_options(ImageAnalysisStatus),
        soil_texture=_options(SoilTexture),
        drought_tolerance=_options(DroughtTolerance),
        risk_level=_options(RiskLevel),
        score_band=_options(ScoreBand),
        data_mode=_options(DataMode),
    )


class EnumCatalogResponse(BaseModel):
    """Wrapper so the catalog can gain metadata without a breaking change."""

    enums: EnumCatalog = Field(description="Every enum, with display labels.")
