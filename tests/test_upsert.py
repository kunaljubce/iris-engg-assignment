import pytest
from sqlalchemy import Engine

from harvester.db.upsert import upsert_records
from tests.db_helpers import make_record, make_run, rows

pytestmark = pytest.mark.db


def test_insert_new_records(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        stats = upsert_records(conn, [make_record("1"), make_record("2")], make_run(conn))

        assert (stats.inserted, stats.updated, stats.duplicates) == (2, 0, [])
        assert [r.source_record_id for r in rows(conn)] == ["1", "2"]


def test_rerun_creates_no_duplicates(db_engine: Engine) -> None:
    batch = [make_record("1"), make_record("2")]
    with db_engine.begin() as conn:
        upsert_records(conn, batch, make_run(conn))
        before = rows(conn)

        stats = upsert_records(conn, batch, make_run(conn))

        assert (stats.inserted, stats.updated) == (0, 2)
        after = rows(conn)
        assert [(r.id, r.created_at) for r in after] == [(r.id, r.created_at) for r in before]


def test_rerun_overwrites_changed_content(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        upsert_records(conn, [make_record("1")], make_run(conn))
        [before] = rows(conn)
        second_run = make_run(conn)

        upsert_records(
            conn, [make_record("1", title="Gewerbegebiet Flughafen, 1. Änderung")], second_run
        )

        [after] = rows(conn)
        assert after.id == before.id
        assert after.title == "Gewerbegebiet Flughafen, 1. Änderung"
        assert after.ingest_run_id == second_run


def test_duplicate_keys_in_one_batch(db_engine: Engine) -> None:
    batch = [make_record("1", title="first"), make_record("1", title="second")]
    with db_engine.begin() as conn:
        stats = upsert_records(conn, batch, make_run(conn))

        assert (stats.inserted, stats.updated) == (1, 0)
        assert [e.kind for e in stats.duplicates] == ["duplicate_in_batch"]
        assert [r.title for r in rows(conn)] == ["second"]


def test_filter_config_change_updates_keywords(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        upsert_records(conn, [make_record("1")], make_run(conn))

        upsert_records(conn, [make_record("1", matched_keywords=["Gewerbe"])], make_run(conn))

        assert [r.matched_keywords for r in rows(conn)] == [["Gewerbe"]]


def test_same_id_from_two_sources_is_two_rows(db_engine: Engine) -> None:
    batch = [make_record("1"), make_record("1", source="leipzig_stadtportal")]
    with db_engine.begin() as conn:
        stats = upsert_records(conn, batch, make_run(conn))

        assert (stats.inserted, stats.duplicates) == (2, [])
