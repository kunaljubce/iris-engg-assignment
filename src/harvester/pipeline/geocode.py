"""Check source coordinates against the city's bounding box, before they reach the database.

Only the state portal gives coordinates (WGS84). A point is kept only if both values are
numbers inside the box; anything else is stored without geometry, with the reason. The
transform to EPSG:25832 happens in the upsert SQL.
"""

from typing import NamedTuple

from harvester.config import BoundingBox
from harvester.models import GeocodeStatus

NO_COORDINATES = "marker has no coordinates"


class GeocodeResult(NamedTuple):
    lon: float | None  # both None unless status is source_coordinates
    lat: float | None
    status: GeocodeStatus
    reason: str | None


def check_coordinates(
    lon: float | str | None, lat: float | str | None, city: str, bbox: BoundingBox
) -> GeocodeResult:
    if lon is None or lat is None or _is_blank(lon) or _is_blank(lat):
        return GeocodeResult(None, None, "unresolved", NO_COORDINATES)

    lon_value, lat_value = _number(lon), _number(lat)
    for name, raw, value, low, high in (
        ("lon", lon, lon_value, bbox.min_lon, bbox.max_lon),
        ("lat", lat, lat_value, bbox.min_lat, bbox.max_lat),
    ):
        if value is None:
            return _rejected(f"{name} {raw!r} is not a number")
        # Written as "inside", so NaN (which fails every comparison) is rejected too
        if not low <= value <= high:
            return _rejected(f"{name} {value} outside {city} [{low}, {high}]")

    return GeocodeResult(lon_value, lat_value, "source_coordinates", None)


def _is_blank(value: float | str) -> bool:
    return isinstance(value, str) and not value.strip()


def _number(value: float | str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _rejected(reason: str) -> GeocodeResult:
    return GeocodeResult(None, None, "rejected_out_of_bounds", reason)
