import pytest

from harvester.config import IndustrialFilterConfig, load_config
from harvester.pipeline.filter import IndustrialFilter
from tests.conftest import SETTINGS_PATH


@pytest.fixture
def industrial_filter() -> IndustrialFilter:
    config = IndustrialFilterConfig(
        keywords=["Gewerbe", "Industrie", "Logistik"],
        case_sensitive_tokens=["GE", "GI", "GVZ"],
        thema_signals=["Gewerbe"],
    )
    return IndustrialFilter(config)


def test_matches_gewerbegebiet_in_title(industrial_filter: IndustrialFilter) -> None:
    result = industrial_filter.check("Bebauungsplan Nr. 3017, Gewerbegebiet Flughafen", [])

    assert result.is_industrial
    assert result.matched_keywords == ["Gewerbe"]


@pytest.mark.parametrize(
    "title",
    [
        "Gewerbestandort Nord",
        "Kleingewerbe am Bahnhof",
        "Logistikzentrum Radefeld",
        "Industriepark Nord",
    ],
)
def test_matches_compounds(industrial_filter: IndustrialFilter, title: str) -> None:
    assert industrial_filter.check(title, []).is_industrial


def test_evidence_contains_full_compound_word(industrial_filter: IndustrialFilter) -> None:
    result = industrial_filter.check("Wohn- und Gewerbepark Süd", [])

    assert result.evidence == ["Gewerbepark"]


def test_matches_ge_case_sensitive(industrial_filter: IndustrialFilter) -> None:
    result = industrial_filter.check("B-Plan GE Nord", [])

    assert result.matched_keywords == ["GE"]
    assert result.evidence == ["GE"]


def test_no_false_positive_on_lowercase_ge(industrial_filter: IndustrialFilter) -> None:
    assert not industrial_filter.check("Einwände gegen ge Wohnbebauung", []).is_industrial


def test_short_codes_not_matched_inside_words(industrial_filter: IndustrialFilter) -> None:
    assert not industrial_filter.check("GIESSEN GEMEINDE GVZE", []).is_industrial


def test_matches_thema_gewerbe_among_several(industrial_filter: IndustrialFilter) -> None:
    result = industrial_filter.check("Bebauungsplan Nr. 485", ["Wohnen", "Gewerbe"])

    assert result.is_industrial
    assert result.matched_keywords == ["thema:Gewerbe"]
    assert result.evidence == []


def test_no_match_returns_false(industrial_filter: IndustrialFilter) -> None:
    result = industrial_filter.check("Wohngebiet Am Park", ["Wohnen"])

    assert not result.is_industrial
    assert result.matched_keywords == []
    assert result.evidence == []


def test_keywords_from_config() -> None:
    industrial_filter = IndustrialFilter(load_config(SETTINGS_PATH).industrial_filter)

    assert industrial_filter.check("Güterverkehrszentrum Leipzig", []).is_industrial
    assert industrial_filter.check("GVZ Dresden", []).matched_keywords == ["GVZ"]
    assert not industrial_filter.check("Schulcampus Mitte", []).is_industrial
