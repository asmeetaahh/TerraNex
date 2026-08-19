"""Geocoding results for farm registration.

The frontend resolves a place name to coordinates through this endpoint rather than
calling a geocoding provider itself — the backend stays the only integration layer.
"""

from pydantic import BaseModel, Field

from app.schemas.common import DataSourceMeta, PaginatedResponse


class Location(BaseModel):
    """A geocoded place."""

    name: str = Field(examples=["Nakuru"])
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    country: str | None = Field(default=None, examples=["Kenya"])
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    region: str | None = Field(default=None, examples=["Nakuru County"])
    elevation_m: float | None = None
    display_name: str = Field(
        description="Pre-formatted label for a picker row.",
        examples=["Nakuru, Nakuru County, Kenya"],
    )


class LocationList(PaginatedResponse[Location]):
    """Response for `GET /api/v1/reference/locations`."""

    meta: DataSourceMeta = Field(
        description="Provenance. `simulated` until a real geocoder is wired in."
    )
