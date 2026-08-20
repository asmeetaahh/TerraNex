"""Open-Meteo response builders, shaped exactly like the real API's payloads.

Shared by the provider tests and the shared-environment tests so both exercise the
same parsing path. Coordinates are Nashik, Maharashtra — a real agricultural centre.
"""

from datetime import date, timedelta

NASHIK_LAT = 19.99727
NASHIK_LON = 73.79096

CANONICAL_PAST_DAYS = 92
CANONICAL_FORECAST_DAYS = 16
CANONICAL_TOTAL = CANONICAL_PAST_DAYS + CANONICAL_FORECAST_DAYS


def daily_payload(days: int = CANONICAL_TOTAL, start: date | None = None) -> dict:
    """A daily block spanning the canonical window."""
    first = start or (date.today() - timedelta(days=CANONICAL_PAST_DAYS))
    times = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    return {
        "latitude": NASHIK_LAT,
        "longitude": NASHIK_LON,
        "timezone": "Asia/Kolkata",
        "timezone_abbreviation": "IST",
        "daily_units": {"temperature_2m_max": "°C", "precipitation_sum": "mm"},
        "daily": {
            "time": times,
            "temperature_2m_max": [33.4 + (i % 3) for i in range(days)],
            "temperature_2m_min": [21.1 + (i % 3) for i in range(days)],
            "temperature_2m_mean": [27.2 + (i % 3) for i in range(days)],
            "relative_humidity_2m_mean": [64 + (i % 5) for i in range(days)],
            "precipitation_sum": [0.0 if i % 4 else 12.5 for i in range(days)],
            "precipitation_hours": [0.0 if i % 4 else 3.0 for i in range(days)],
            "wind_speed_10m_max": [14.2 for _ in range(days)],
            "et0_fao_evapotranspiration": [5.42 for _ in range(days)],
            "shortwave_radiation_sum": [21.3 for _ in range(days)],
            "cloud_cover_mean": [40 for _ in range(days)],
            "weather_code": [61 if i % 4 == 0 else 1 for i in range(days)],
        },
    }


def hourly_payload(hours: int = 72) -> dict:
    """An hourly block plus a current block, as the near-term request returns."""
    base = date.today()
    times = [
        (f"{(base + timedelta(days=h // 24)).isoformat()}T{h % 24:02d}:00") for h in range(hours)
    ]
    return {
        "latitude": NASHIK_LAT,
        "longitude": NASHIK_LON,
        "timezone": "Asia/Kolkata",
        "current": {
            "time": f"{base.isoformat()}T09:00",
            "temperature_2m": 28.6,
            "relative_humidity_2m": 71,
            "apparent_temperature": 31.2,
            "precipitation": 0.4,
            "wind_speed_10m": 11.5,
            "wind_direction_10m": 245,
            "cloud_cover": 55,
            "pressure_msl": 1008.4,
            "weather_code": 61,
        },
        "hourly": {
            "time": times,
            "temperature_2m": [26.0 + (h % 6) for h in range(hours)],
            "relative_humidity_2m": [70 + (h % 10) for h in range(hours)],
            "precipitation": [0.1 for _ in range(hours)],
            "wind_speed_10m": [10.0 for _ in range(hours)],
            "et0_fao_evapotranspiration": [0.22 for _ in range(hours)],
            "shortwave_radiation": [420.0 for _ in range(hours)],
            "soil_moisture_0_to_1cm": [0.24 for _ in range(hours)],
            "soil_temperature_0cm": [25.5 for _ in range(hours)],
        },
    }
