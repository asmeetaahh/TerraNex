"""Risk assessments produced by the deterministic risk engine.

Every numeric field here is computed in pure Python from provider data. The AI only
ever fills `explanation` — it interprets these numbers, it never produces them. That
separation is what makes the scores reproducible and unit-testable.
"""

from pydantic import BaseModel, Field

from app.schemas.common import ReasonCode, RiskLevel, ScoredFactor


class WeatherRisk(BaseModel):
    """Near-term weather threats to the crop.

    Response for `GET /api/v1/farms/{farm_id}/risks/weather`.
    """

    level: RiskLevel
    score: int = Field(ge=0, le=100, description="Higher means more risk.")
    forecast_window_days: int = Field(ge=1, examples=[7])

    heat_stress_days: int = Field(
        ge=0, description="Forecast days above the crop's optimal maximum temperature."
    )
    frost_risk_days: int = Field(ge=0)
    heavy_rain_days: int = Field(ge=0)
    high_wind_days: int = Field(ge=0)
    longest_dry_spell_days: int = Field(ge=0)
    max_temp_c: float | None = None
    min_temp_c: float | None = None
    total_precipitation_mm: float | None = Field(default=None, ge=0)

    drivers: list[str] = Field(
        default_factory=list,
        description="Which computed conditions drove this level.",
        examples=[["4 forecast days above 34 °C during flowering"]],
    )
    factors: list[ScoredFactor] = Field(default_factory=list)
    explanation: str = Field(description="Narrative over the values above.")


class WaterRisk(BaseModel):
    """Irrigation and water-stress assessment from a soil water balance.

    Response for `GET /api/v1/farms/{farm_id}/risks/water`.
    """

    level: RiskLevel
    score: int = Field(ge=0, le=100)

    water_balance_mm: float = Field(
        description=(
            "Σ precipitation − Σ(ET₀ × Kc) over the assessment window. Negative means deficit."
        )
    )
    deficit_mm: float = Field(
        ge=0, description="Shortfall against crop demand. Zero when balance is non-negative."
    )
    total_precipitation_mm: float | None = Field(default=None, ge=0)
    total_crop_water_demand_mm: float | None = Field(default=None, ge=0)
    soil_moisture_pct: float | None = Field(default=None, ge=0, le=100)
    water_holding_capacity_mm: float | None = Field(default=None, ge=0)

    days_until_stress: int | None = Field(
        default=None,
        ge=0,
        description="Projected days before the crop enters water stress. Null if not projected.",
    )
    recommended_irrigation_mm: float = Field(
        ge=0, description="Suggested application to close the deficit."
    )
    irrigation_window: str | None = Field(
        default=None, description="When to apply it.", examples=["within 48 hours"]
    )
    irrigation_efficiency_note: str | None = Field(
        default=None,
        description="How the farm's irrigation type affects the recommendation.",
    )

    drivers: list[str] = Field(default_factory=list)
    factors: list[ScoredFactor] = Field(default_factory=list)
    explanation: str


class DiseaseRiskItem(BaseModel):
    """One pathogen evaluated against the farm's conditions.

    Produced by an explicit agronomic rule (temperature window, humidity threshold,
    consecutive-hour duration, growth stage) — not by the AI.
    """

    name: str = Field(examples=["Late blight"])
    pathogen: str | None = Field(default=None, examples=["Phytophthora infestans"])
    crop_code: str | None = Field(default=None, examples=["potato"])
    level: RiskLevel
    probability: float = Field(
        ge=0, le=1, description="Rule-derived likelihood under current conditions."
    )
    triggering_conditions: list[str] = Field(
        default_factory=list,
        description="The rule clauses that matched.",
        examples=[["RH above 85% for 14 consecutive hours", "Mean temp 18 °C"]],
    )
    preventive_actions: list[str] = Field(default_factory=list)
    scouting_advice: str | None = None

    reasons: list[ReasonCode] = Field(
        default_factory=list,
        description=(
            "The same evidence as `triggering_conditions`, as data rather than prose — "
            "one entry per matched rule clause. Lets a client state why this pathogen "
            "was flagged in any language. Empty when no rule matched."
        ),
    )


class DiseaseRisk(BaseModel):
    """Aggregate disease pressure.

    Response for `GET /api/v1/farms/{farm_id}/risks/disease`.
    """

    level: RiskLevel
    score: int = Field(ge=0, le=100)
    conditions_summary: str = Field(
        description="The weather pattern driving pressure, in one sentence."
    )
    risks: list[DiseaseRiskItem] = Field(
        default_factory=list, description="Per-pathogen assessments, highest risk first."
    )
    factors: list[ScoredFactor] = Field(default_factory=list)
    explanation: str
