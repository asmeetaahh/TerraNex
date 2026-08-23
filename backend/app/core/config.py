"""Application settings, loaded from environment / .env and validated once at import."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Application ----
    APP_NAME: str = "TerraNex API"
    APP_VERSION: str = "0.1.0"
    APP_ENV: Literal["local", "test", "staging", "production"] = "local"
    LOG_LEVEL: str = "INFO"
    API_V1_PREFIX: str = "/api/v1"

    # ---- CORS ----
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ---- Database ----
    DATABASE_URL: str | None = None

    # ---- Supabase ----
    SUPABASE_URL: str | None = None
    SUPABASE_ANON_KEY: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: str | None = None
    SUPABASE_JWT_SECRET: str | None = None
    SUPABASE_STORAGE_BUCKET: str = "crop-images"

    # ---- Auth ----
    # False => requests are attributed to the seeded demo user. The frontend's
    # unblock switch: every screen can be built before login is wired up.
    ENABLE_AUTH: bool = False
    DEMO_USER_EMAIL: str = "demo@terranex.app"

    # ---- AI ----
    AI_PROVIDER: Literal["mock", "gemini"] = "mock"
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    AI_TIMEOUT_S: float = 30.0
    AI_TEMPERATURE: float = 0.3

    # ---- External providers ----
    # Default to the real providers. TerraNex is for farms anywhere, and only
    # Open-Meteo resolves arbitrary places and reports their actual weather; the
    # simulator models one climate archetype and applies it worldwide, which is
    # wrong outside humid seasonally-rainy regions. Open-Meteo needs no API key,
    # so a fresh clone works correctly with no configuration.
    #
    # `simulated` remains fully supported and is selected explicitly for offline
    # development, deterministic demos, and the test suite (see tests/conftest.py).
    # `nasa_power` is opt-in and never selected automatically. It is a daily historical
    # record that lags by roughly three days and carries no hourly series, so it cannot
    # serve forecasts or the disease engine's consecutive-hour rules. Open-Meteo remains
    # the default, and the degradation ladder is unchanged.
    WEATHER_PROVIDER: Literal["open_meteo", "nasa_power", "simulated"] = "open_meteo"
    GEOCODING_PROVIDER: Literal["open_meteo", "simulated"] = "open_meteo"
    SOIL_PROVIDER: Literal["soilgrids", "simulated"] = "soilgrids"
    # When a live provider fails, fall back to the simulator (clearly marked
    # `simulated`) instead of returning `unavailable`. Useful for demos; never
    # relabels the data.
    WEATHER_FALLBACK_TO_SIMULATION: bool = True
    SOIL_FALLBACK_TO_SIMULATION: bool = True

    PROVIDER_TIMEOUT_S: float = 8.0
    PROVIDER_MAX_RETRIES: int = 2
    # Wall-clock budget for one provider operation, covering every attempt and the
    # backoff between them. Without it, 3 attempts x 8s + backoff let a single
    # unresponsive upstream block a request for ~25s per call — and weather makes
    # two, so a dashboard could hang for ~33s. This bounds the whole operation.
    PROVIDER_DEADLINE_S: float = 12.0
    CACHE_TTL_WEATHER_S: int = 1800
    CACHE_TTL_SOIL_S: int = 2_592_000
    CACHE_TTL_VEGETATION_S: int = 21_600
    CACHE_TTL_GEOCODE_S: int = 86_400

    # ---- Uploads ----
    MAX_UPLOAD_MB: int = 10
    ALLOWED_IMAGE_TYPES: list[str] = ["image/jpeg", "image/png", "image/webp"]

    # ---- Analysis ----
    ANALYSIS_CACHE_TTL_S: int = 3600

    # ---- Demo ----
    SEED_DEMO_DATA: bool = True

    @field_validator("CORS_ORIGINS", "ALLOWED_IMAGE_TYPES", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Accept both a comma-separated string (from .env) and a real list."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def docs_url(self) -> str | None:
        """Swagger stays on in every non-production environment — it is the
        frontend developer's live API playground."""
        return None if self.is_production else "/docs"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
