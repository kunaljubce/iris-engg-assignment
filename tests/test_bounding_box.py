import pytest
from sqlalchemy import Engine

from harvester.db.upsert import upsert_records
from tests.db_helpers import make_record, make_run, rows

pytestmark = pytest.mark.db

# Dresden Frauenkirche: WGS84 in, EPSG:25832 out, computed independently with pyproj
CONTROL_LON, CONTROL_LAT = 13.7416, 51.0519
CONTROL_X, CONTROL_Y = 832265.29, 5666299.84


def test_out_of_bounds_stored_with_null_geometry(db_engine: Engine) -> None:
    rejected = make_record(
        lon=None,
        lat=None,
        geocode_status="rejected_out_of_bounds",
        geocode_reason="lon 51.05 outside dresden [13.55, 13.95]",
    )
    with db_engine.begin() as conn:
        upsert_records(conn, [rejected], make_run(conn))

        [row] = rows(conn)
        assert row.srid is None
        assert (row.geocode_status, row.geocode_reason) == (
            "rejected_out_of_bounds",
            "lon 51.05 outside dresden [13.55, 13.95]",
        )


def test_in_bounds_stored_with_geometry(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        upsert_records(conn, [make_record()], make_run(conn))

        [row] = rows(conn)
        assert row.srid == 25832
        assert row.geocode_status == "source_coordinates"


def test_transform_control_point(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        upsert_records(conn, [make_record(lon=CONTROL_LON, lat=CONTROL_LAT)], make_run(conn))

        [row] = rows(conn)
        assert row.x == pytest.approx(CONTROL_X, abs=1.0)
        assert row.y == pytest.approx(CONTROL_Y, abs=1.0)
