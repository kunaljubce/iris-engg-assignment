from datetime import date

import pytest

from harvester.config import HarvesterConfig
from harvester.models import ScrapedRecord
from harvester.pipeline.normalise import normalise, parse_publication_date
from tests.conftest import MockClient, load_fixture

DETAIL_URL = "https://buergerbeteiligung.sachsen.de/portal/dresden/beteiligung/themen/{}"


def sachsen_record(
    record_id: str = "1069133", title: str = "Gewerbecampus Am Werk"
) -> ScrapedRecord:
    return ScrapedRecord(
        source="sachsen_portal",
        source_record_id=record_id,
        city="dresden",
        title=title,
        detail_url=DETAIL_URL.format(record_id),
        location_text="Am Werk 1",
        lon=13.76,
        lat=51.12,
        raw_data={"marker": {"id": record_id}},
    )


def test_publication_date_is_zeitraum_start(
    mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    client = mock_client(
        {DETAIL_URL.format("1069133"): load_fixture("sachsen_dresden_detail.html")}
    )

    [record] = normalise([sachsen_record()], client, harvester_config).records

    assert record.publication_date == date(2026, 10, 12)
    assert record.raw_data["zeitraum"] == "Zeitraum 12.10.2026 bis 13.11.2026"
    assert record.matched_keywords == ["Gewerbe"]
    assert record.raw_data["filter_evidence"] == ["Gewerbecampus"]
    assert (record.lon, record.lat, record.geocode_status) == (13.76, 51.12, "source_coordinates")
    assert record.quality_flags == []


@pytest.mark.parametrize(
    ("zeitraum", "expected"),
    [
        ("Zeitraum 12.10.2026 bis 13.11.2026", (date(2026, 10, 12), None)),
        ("Zeitraum 1.2.2026 bis 3.3.2026", (date(2026, 2, 1), None)),
        (None, (None, "missing_publication_date")),
        ("Zeitraum demnächst", (None, "unparseable_date")),
        ("Zeitraum 31.02.2026 bis 13.03.2026", (None, "unparseable_date")),
    ],
)
def test_unparseable_date_flagged_not_raised(
    zeitraum: str | None, expected: tuple[date | None, str | None]
) -> None:
    assert parse_publication_date(zeitraum) == expected


def test_marker_without_coordinates_or_address_is_unresolved(
    mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    client = mock_client(
        {DETAIL_URL.format("1069133"): load_fixture("sachsen_dresden_detail.html")}
    )
    no_location = sachsen_record().model_copy(
        update={"lon": None, "lat": None, "location_text": None}
    )

    [record] = normalise([no_location], client, harvester_config).records

    assert (record.lon, record.lat, record.geocode_status) == (None, None, "unresolved")
    assert record.geocode_reason == "marker has no coordinates"
    assert record.quality_flags == ["missing_location"]


def test_detail_fetched_only_for_matches(
    mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    # Only the match is routed; a request for the other record would fail the test
    client = mock_client(
        {DETAIL_URL.format("1069133"): load_fixture("sachsen_dresden_detail.html")}
    )
    records = [sachsen_record(), sachsen_record("1", title="Wohngebiet Am Park")]

    result = normalise(records, client, harvester_config)

    assert [r.source_record_id for r in result.records] == ["1069133"]


def test_detail_page_404_flagged(
    mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    client = mock_client({DETAIL_URL.format("1069133"): 404})

    [record] = normalise([sachsen_record()], client, harvester_config).records

    assert record.quality_flags == ["detail_page_failed", "missing_publication_date"]
    assert record.raw_data["detail_error"] == "HTTP 404"
    # Listing data is kept
    assert (record.title, record.location_text) == ("Gewerbecampus Am Werk", "Am Werk 1")
    assert record.geocode_status == "source_coordinates"


def test_out_of_bounds_marker_is_rejected(
    mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    client = mock_client(
        {DETAIL_URL.format("1069133"): load_fixture("sachsen_dresden_detail.html")}
    )
    swapped = sachsen_record().model_copy(update={"lon": 51.12, "lat": 13.76})

    [record] = normalise([swapped], client, harvester_config).records

    assert (record.lon, record.lat, record.geocode_status) == (None, None, "rejected_out_of_bounds")
    assert record.geocode_reason == "lon 51.12 outside dresden [13.55, 13.95]"


def test_non_industrial_records_dropped_and_counted(
    mock_client: MockClient, harvester_config: HarvesterConfig
) -> None:
    client = mock_client(
        {DETAIL_URL.format("1069133"): load_fixture("sachsen_dresden_detail.html")}
    )
    records = [
        sachsen_record("1", title="Wohngebiet Am Park"),
        sachsen_record("2", title="Schulcampus Nord"),
        sachsen_record(),
    ]

    result = normalise(records, client, harvester_config)

    assert len(result.records) == 1
    assert result.filtered_out == 2
