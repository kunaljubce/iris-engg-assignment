from harvester.adapters.leipzig_stadtportal import (
    listing_url,
    parse_next_page,
    parse_rows,
    scrape,
)
from harvester.config import LeipzigStadtSource, ScrapingConfig
from tests.conftest import MockClient, load_fixture

SOURCE = LeipzigStadtSource(
    type="leipzig_stadt",
    name="leipzig_stadtportal",
    base_url="https://www.leipzig.de",
    listing_path="/rathaus/buergerbeteiligung/buergerbeteiligung-und-planinformation/bebauungsplaene",
    status_filter_id=252,
)
PAGE_URL = listing_url(SOURCE)

# The real button markup, from SOURCES.md
NEXT_BUTTON = (
    '<button class="btn btn-secondary start-ajax-pagination" data-next="2">Mehr laden</button>'
)


def row(record_id: str, status: str = "im Verfahren", title: str = "Gewerbegebiet Nord") -> str:
    """One table row in the register's markup."""
    return f"""
    <tr id="project-{record_id}">
      <td class="col-image"></td>
      <td><a class="filterlist-detailpage" href="/detail/projekt/{record_id}">
        <span>{title}</span><span class="icon" aria-hidden="true">arrow_forward</span></a></td>
      <td><ul><li>{status}</li></ul></td>
      <td><ul><li>Nord / Eutritzsch</li></ul></td>
      <td><ul><li>Gewerbe</li></ul></td>
    </tr>"""


def table(*rows: str, extra: str = "") -> str:
    return f"<table><tbody>{''.join(rows)}</tbody></table>{extra}"


def test_parses_rows() -> None:
    records, errors = parse_rows(load_fixture("leipzig_stadt_listing_im_verfahren.html"), PAGE_URL)

    assert errors == []
    assert len(records) == 39
    nr_911 = next(r for r in records if r.source_record_id == "11196578")
    assert nr_911.source == "leipzig_stadtportal"
    assert nr_911.city == "leipzig"
    assert nr_911.title.startswith("Bebauungsplan Nr. 911 Industriegebiet Am Flughafen")
    assert "arrow_forward" not in nr_911.title
    assert nr_911.detail_url.startswith("https://www.leipzig.de/rathaus/buergerbeteiligung/")
    assert nr_911.raw_data["status"] == ["im Verfahren"]
    assert (nr_911.lon, nr_911.lat, nr_911.zeitraum) == (None, None, None)


def test_splits_districts_and_thema() -> None:
    records, _ = parse_rows(load_fixture("leipzig_stadt_listing_im_verfahren.html"), PAGE_URL)

    nr_485 = next(r for r in records if r.source_record_id == "16187322")
    assert nr_485.location_text == "West / Schönau; Alt-West / Burghausen-Rückmarsdorf"
    assert nr_485.thema == ["Gewerbe", "Sondergebiet", "Sonstiges"]


def test_row_without_thema_has_empty_list() -> None:
    records, _ = parse_rows(load_fixture("leipzig_stadt_listing_im_verfahren.html"), PAGE_URL)

    nr_422 = next(r for r in records if r.source_record_id == "10559791")
    assert nr_422.thema == []


def test_unexpected_status_is_an_error() -> None:
    # If the portal ignored the "im Verfahren" filter, plans in force must not be stored
    records, errors = parse_rows(table(row("1"), row("2", status="rechtskräftig")), PAGE_URL)

    assert [r.source_record_id for r in records] == ["1"]
    assert [e.kind for e in errors] == ["unexpected_status"]


def test_handles_malformed_rows() -> None:
    no_title = row("2", title="")
    # Title and status cells only: district and Thema columns missing
    too_few_cells = (
        row("3")
        .replace("<td><ul><li>Nord / Eutritzsch</li></ul></td>", "")
        .replace("<td><ul><li>Gewerbe</li></ul></td>", "")
    )
    page = table(row("1"), no_title, too_few_cells)

    records, errors = parse_rows(page, PAGE_URL)

    assert [r.source_record_id for r in records] == ["1"]
    assert [e.kind for e in errors] == ["missing_title", "unexpected_row_layout"]
    assert errors[0].snippet is not None and 'id="project-2"' in errors[0].snippet


def test_parse_next_page() -> None:
    assert parse_next_page(table(row("1"), extra=NEXT_BUTTON)) == 2
    assert parse_next_page(load_fixture("leipzig_stadt_listing_im_verfahren.html")) is None


def test_follows_next_page(mock_client: MockClient, scraping_config: ScrapingConfig) -> None:
    client = mock_client(
        {
            listing_url(SOURCE, 1): table(row("1"), extra=NEXT_BUTTON),
            listing_url(SOURCE, 2): load_fixture("leipzig_stadt_listing_im_verfahren.html"),
        }
    )

    result = scrape(client, SOURCE, scraping_config)

    assert len(result.records) == 1 + 39
    assert result.errors == []


def test_failed_page_is_an_error(mock_client: MockClient, scraping_config: ScrapingConfig) -> None:
    client = mock_client({listing_url(SOURCE, 1): 503})

    result = scrape(client, SOURCE, scraping_config)

    assert result.records == []
    assert [e.kind for e in result.errors] == ["retries_exhausted"]
