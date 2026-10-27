import json

import pytest

from harvester.adapters.fetch import FetchError
from harvester.adapters.sachsen_portal import (
    fetch_detail,
    listing_url,
    parse_detail,
    parse_expected_count,
    parse_listing,
    scrape,
)
from harvester.config import City, SachsenSource, ScrapingConfig
from tests.conftest import MockClient, load_fixture

BASE = "https://buergerbeteiligung.sachsen.de"
LISTING_URL = f"{BASE}/portal/dresden/beteiligung/themen?format=Bauleitplan&status=BEENDETE"


def markers_page(markers: list[object], extra: str = "") -> str:
    """Minimal listing in the portal's markup: marker call, then optional cards/paginator."""
    return (
        f"<script>map.drawMarkers({json.dumps(markers)});drawMarkers(markerData);</script>{extra}"
    )


def marker(record_id: int, title: str = "Gewerbegebiet Nord") -> dict[str, object]:
    return {
        "id": record_id,
        "kategorie": title,
        "href": f"/portal/dresden/beteiligung/themen/{record_id}",
        "latitude": 51.05,
        "longitude": 13.74,
        "adresse": "Feldstraße, 01109 Dresden",
    }


def test_parses_markers() -> None:
    records, errors = parse_listing(
        load_fixture("sachsen_dresden_listing_beendet.html"), LISTING_URL, "dresden"
    )

    assert errors == []
    gewerbe = next(r for r in records if r.source_record_id == "1066719")
    assert gewerbe.source == "sachsen_portal"
    assert gewerbe.city == "dresden"
    assert (
        gewerbe.title == "Bebauungsplan Nr. 3017, Dresden-Hellerau Nr. 13, Gewerbegebiet Flughafen"
    )
    assert gewerbe.detail_url == f"{BASE}/portal/dresden/beteiligung/themen/1066719"
    assert gewerbe.location_text == "Feldstraße, 01109 Dresden"
    assert isinstance(gewerbe.lon, float) and 13.55 <= gewerbe.lon <= 13.95
    assert isinstance(gewerbe.lat, float) and 50.95 <= gewerbe.lat <= 51.18
    assert gewerbe.raw_data["listing_url"] == LISTING_URL


def test_listing_has_all_results() -> None:
    page = load_fixture("sachsen_dresden_listing_beendet.html")

    records, _ = parse_listing(page, LISTING_URL, "dresden")

    # 52 markers although only 10 cards are rendered; the paginator says "1 - 10 von 52"
    assert len(records) == 52
    assert parse_expected_count(page) == 52


def test_unescapes_title() -> None:
    page = markers_page(
        [marker(1, "Bebauungsplan &quot;Am Werk&quot;")], '<div class="nbp-teaser"></div>'
    )

    records, _ = parse_listing(page, LISTING_URL, "dresden")

    assert records[0].title == 'Bebauungsplan "Am Werk"'


def test_count_mismatch_logged() -> None:
    paginator = '<span class="ui-paginator-current">1 - 10 von 3</span>'
    page = markers_page([marker(1), marker(2)], paginator)

    records, errors = parse_listing(page, LISTING_URL, "dresden")

    assert len(records) == 2
    assert [e.kind for e in errors] == ["count_mismatch"]


def test_parses_zeitraum_from_detail() -> None:
    zeitraum = parse_detail(load_fixture("sachsen_dresden_detail.html"), "1069133")

    assert zeitraum == "Zeitraum 12.10.2026 bis 13.11.2026"


def test_redirect_to_listing_gives_no_zeitraum() -> None:
    # Listing cards carry the same Zeitraum element; their dates must not be taken
    page = load_fixture("sachsen_dresden_listing_beendet.html")

    assert parse_detail(page, "1069133") is None


def test_other_records_detail_page_gives_no_zeitraum() -> None:
    assert parse_detail(load_fixture("sachsen_dresden_detail.html"), "1066719") is None


def test_handles_empty_listing() -> None:
    records, errors = parse_listing(
        "<html><body>Keine Ergebnisse</body></html>", LISTING_URL, "dresden"
    )

    assert (records, errors) == ([], [])


def test_cards_without_markers_is_an_error() -> None:
    page = '<div class="nbp-teaser"></div><div class="nbp-teaser"></div>'

    records, errors = parse_listing(page, LISTING_URL, "dresden")

    assert records == []
    assert [e.kind for e in errors] == ["markers_not_found"]


def test_handles_malformed_markers() -> None:
    no_id = marker(2)
    del no_id["id"]
    no_title = marker(3, title="  ")
    page = markers_page(
        [marker(1), no_id, no_title], '<span class="ui-paginator-current">1 - 3 von 3</span>'
    )

    records, errors = parse_listing(page, LISTING_URL, "dresden")

    assert [r.source_record_id for r in records] == ["1"]
    assert [e.kind for e in errors] == ["missing_id", "missing_title"]
    assert errors[0].snippet is not None and "Gewerbegebiet Nord" in errors[0].snippet


def test_scrape_reads_every_city_and_status(
    mock_client: MockClient, scraping_config: ScrapingConfig
) -> None:
    source = SachsenSource(
        type="sachsen",
        name="sachsen_portal",
        base_url=BASE,
        cities=[City(slug="dresden", label="Dresden"), City(slug="leipzig", label="Leipzig")],
        format_filter="Bauleitplan",
        status_filters=["BEENDETE"],
    )
    client = mock_client(
        {
            listing_url(source, "dresden", "BEENDETE"): load_fixture(
                "sachsen_dresden_listing_beendet.html"
            ),
            listing_url(source, "leipzig", "BEENDETE"): 404,
        }
    )

    result = scrape(client, source, scraping_config)

    # One failed listing doesn't stop the others
    assert len(result.records) == 52
    assert [(e.kind, e.url) for e in result.errors] == [
        ("http_4xx", listing_url(source, "leipzig", "BEENDETE"))
    ]


def test_fetch_detail_adds_zeitraum(
    mock_client: MockClient, scraping_config: ScrapingConfig
) -> None:
    records, _ = parse_listing(
        markers_page([marker(1069133)], '<div class="nbp-teaser"></div>'), LISTING_URL, "dresden"
    )
    client = mock_client({records[0].detail_url: load_fixture("sachsen_dresden_detail.html")})

    detailed = fetch_detail(client, records[0], scraping_config)

    assert detailed.zeitraum == "Zeitraum 12.10.2026 bis 13.11.2026"
    assert detailed.title == records[0].title


def test_fetch_detail_failure_raises(
    mock_client: MockClient, scraping_config: ScrapingConfig
) -> None:
    records, _ = parse_listing(
        markers_page([marker(1)], '<div class="nbp-teaser"></div>'), LISTING_URL, "dresden"
    )
    client = mock_client({records[0].detail_url: 404})

    with pytest.raises(FetchError):
        fetch_detail(client, records[0], scraping_config)
