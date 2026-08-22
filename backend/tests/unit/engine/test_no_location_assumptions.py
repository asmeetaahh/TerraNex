"""TerraNex is global and BRICS-ready, and this is what keeps it that way.

The requirement is easy to state and easy to erode. Nobody sets out to write an
India-only risk engine; it happens one convenient constant at a time — a hardcoded
`Asia/Kolkata`, a monsoon month, a `if country == "IN"` shortcut for a demo, a
threshold tuned until Bengaluru looked right.

The existing twelve-site provider matrix in `tests/fixtures/open_meteo.py` proves the
*provider* path works anywhere. This proves the *engine and ruleset* carry no
geography at all: risk must derive from coordinates, crop parameters and observed
weather, never from a place.

`tests/api/test_global_locations.py` already registers every site under the decoy name
`"Bengaluru Farm"` for the same reason — so that reaching the right answer proves the
coordinates carried the request rather than the label.

The negative control at the bottom is what stops this file becoming decoration.
"""

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]
ENGINE_DIR = BACKEND / "app" / "engine"
RULES_DIR = BACKEND / "app" / "rules"

#: Country and demonym words that must not appear in engine or ruleset source.
#: BRICS members first, then the places most likely to be hardcoded by accident.
COUNTRY_WORDS = [
    "brazil", "brazilian",
    "russia", "russian",
    "india", "indian",
    "china", "chinese",
    "africa", "african",
    "egypt", "egyptian",
    "ethiopia", "ethiopian",
    "iran", "iranian",
    "emirates", "emirati",
    "indonesia", "indonesian",
    "saudi", "arabia", "arabian",
]  # fmt: skip

CITY_WORDS = [
    "bengaluru", "bangalore", "mumbai", "delhi", "chennai", "hyderabad",
    "kolkata", "pune", "nashik", "nakuru", "beijing", "shanghai",
    "moscow", "cairo", "tehran", "riyadh", "jakarta", "dubai",
    "brasilia", "johannesburg",
]  # fmt: skip

#: ISO-3166 alpha-2 codes for current BRICS members, matched only as standalone
#: quoted literals so ordinary words are not flagged.
COUNTRY_CODES = ("BR", "RU", "IN", "CN", "ZA", "EG", "ET", "IR", "AE", "ID", "SA")

#: A hardcoded IANA zone means the engine is deciding where it is.
TIMEZONE_PATTERN = re.compile(
    r"['\"](?:Africa|America|Asia|Europe|Australia|Indian|Pacific|Atlantic)/[A-Za-z_]+['\"]"
)

#: Month and season names in engine code almost always mean a hemisphere assumption —
#: a "monsoon season" or "planting month" that is wrong six months later and 20 degrees
#: south. "May" is deliberately absent: it is too common as an ordinary English verb to
#: distinguish from the month without parsing, and flagging it would only train people
#: to reword honest prose.
MONTH_PATTERN = re.compile(
    r"(?<![a-z])(january|february|march|april|june|july|august|september|october|"
    r"november|december|monsoon|kharif|rabi)(?![a-z])",
    re.IGNORECASE,
)


#: `\b` is the wrong boundary here: `_` counts as a word character, so `\bbengaluru\b`
#: does not match `BENGALURU_LAT` — which is exactly the form a hardcoded constant
#: takes. Bounding on letters instead catches identifiers, quoted strings and prose
#: alike. The negative control below is what surfaced this.
def _word_pattern(word: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z]){re.escape(word)}(?![a-z])")


def source_files() -> list[Path]:
    return sorted([*ENGINE_DIR.rglob("*.py"), *RULES_DIR.rglob("*.py"), *RULES_DIR.rglob("*.yaml")])


def _strip_comments(path: Path, text: str) -> str:
    """Remove comment lines.

    Prose explaining *why* the engine is location-independent necessarily names
    places — this very requirement is documented in those comments. What matters is
    that no place name reaches executable code or rule data.
    """
    marker = "#"
    cleaned = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(marker):
            continue
        cleaned.append(line.split(marker)[0] if marker in line else line)
    return "\n".join(cleaned)


def findings(path: Path) -> list[str]:
    """Location assumptions in `path`, ignoring comments."""
    body = _strip_comments(path, path.read_text(encoding="utf-8"))
    lowered = body.lower()
    found: list[str] = []

    for word in COUNTRY_WORDS + CITY_WORDS:
        if _word_pattern(word).search(lowered):
            found.append(f"place name {word!r}")

    for code in COUNTRY_CODES:
        if re.search(rf"['\"]{code}['\"]", body):
            found.append(f"country code {code!r}")

    for match in TIMEZONE_PATTERN.findall(body):
        found.append(f"hardcoded timezone {match}")

    for match in set(MONTH_PATTERN.findall(body)):
        found.append(f"calendar/season assumption {match!r}")

    return found


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------


def test_the_scan_actually_finds_source_files() -> None:
    """Safeguard: without this, an empty file list would make every check below pass."""
    files = source_files()

    assert len(files) >= 5, f"only found {len(files)} files under {ENGINE_DIR} and {RULES_DIR}"
    assert any(f.suffix == ".yaml" for f in files), "no ruleset data was scanned"
    assert any(f.suffix == ".py" for f in files), "no engine source was scanned"


@pytest.mark.parametrize("path", source_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_location_assumptions(path: Path) -> None:
    hits = findings(path)

    assert not hits, (
        f"{path.parent.name}/{path.name} contains location-specific data: "
        + "; ".join(hits)
        + ". Risk must derive from coordinates, crop parameters and weather."
    )


def test_the_guard_detects_a_location_assumption(tmp_path: Path) -> None:
    """Negative control. Each of these is a real way the requirement has been broken
    in projects like this one."""
    offender = tmp_path / "regression.py"
    offender.write_text(
        "\n".join(
            [
                "DEFAULT_TIMEZONE = 'Asia/Kolkata'",
                "if farm.country_code == 'IN':",
                "    kc *= 1.1",
                "MONSOON_START = 'june'",
                "BENGALURU_LAT = 12.97",
            ]
        ),
        encoding="utf-8",
    )

    hits = findings(offender)

    assert any("timezone" in h for h in hits)
    assert any("country code" in h for h in hits)
    assert any("bengaluru" in h for h in hits)
    assert any("june" in h for h in hits)


def test_the_guard_ignores_explanatory_comments(tmp_path: Path) -> None:
    """Complement to the control: documenting the requirement must not violate it,
    or the honest thing to do would be to stop writing the comments."""
    documented = tmp_path / "documented.py"
    documented.write_text(
        "\n".join(
            [
                "# Coefficients are global. A crop in India and one in Brazil resolve",
                "# identically and diverge only through observed weather.",
                "KC_MID = 1.15  # not Bengaluru-specific",
            ]
        ),
        encoding="utf-8",
    )

    assert findings(documented) == []


def test_the_ruleset_is_keyed_on_crops_not_places() -> None:
    """The positive form of the requirement, asserted against real data.

    Every top-level key under `crops:` must be a crop code from the catalog — never a
    region, country or climate zone.
    """
    from app.db.seed import load_crop_catalog
    from app.rules.registry import crop_coefficients

    catalog = {crop.code for crop in load_crop_catalog()}
    ruleset_crops = set(crop_coefficients()["crops"])

    assert ruleset_crops <= catalog, (
        "ruleset defines coefficients for entries that are not crops in the catalog: "
        f"{sorted(ruleset_crops - catalog)}"
    )
