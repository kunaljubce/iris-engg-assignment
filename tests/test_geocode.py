import math

import pytest

from harvester.config import BoundingBox
from harvester.pipeline.geocode import check_coordinates

DRESDEN = BoundingBox(min_lon=13.55, min_lat=50.95, max_lon=13.95, max_lat=51.18)


def test_in_bounds_is_source_coordinates() -> None:
    # Gewerbegebiet Flughafen, as the marker gives it (strings)
    result = check_coordinates("13.7642", "51.1214", "dresden", DRESDEN)

    assert result == (13.7642, 51.1214, "source_coordinates", None)


@pytest.mark.parametrize(("lon", "lat"), [(None, None), ("", ""), (13.76, None), ("  ", 51.12)])
def test_missing_coordinates_unresolved(lon: float | str | None, lat: float | str | None) -> None:
    result = check_coordinates(lon, lat, "dresden", DRESDEN)

    assert result == (None, None, "unresolved", "marker has no coordinates")


def test_rejects_out_of_bounds() -> None:
    # Leipzig's Augustusplatz given for a Dresden record
    result = check_coordinates(12.38, 51.34, "dresden", DRESDEN)

    assert (result.lon, result.lat, result.status) == (None, None, "rejected_out_of_bounds")
    assert result.reason == "lon 12.38 outside dresden [13.55, 13.95]"


def test_rejects_swapped_lat_lon() -> None:
    result = check_coordinates(51.1214, 13.7642, "dresden", DRESDEN)

    assert result.status == "rejected_out_of_bounds"
    assert result.reason == "lon 51.1214 outside dresden [13.55, 13.95]"


@pytest.mark.parametrize(
    ("lon", "reason"),
    [
        ("abc", "lon 'abc' is not a number"),
        ("13,76", "lon '13,76' is not a number"),
        (math.nan, "lon nan outside dresden [13.55, 13.95]"),
        ("inf", "lon inf outside dresden [13.55, 13.95]"),
    ],
)
def test_rejects_non_numeric_and_non_finite(lon: float | str, reason: str) -> None:
    result = check_coordinates(lon, 51.12, "dresden", DRESDEN)

    assert (result.lon, result.lat, result.status) == (None, None, "rejected_out_of_bounds")
    assert result.reason == reason
