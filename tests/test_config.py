from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from harvester.config import DatabaseSettings, LeipzigStadtSource, SachsenSource, load_config

# Characters that break a hand-written postgresql:// URL unless percent-encoded
TRICKY_PASSWORD = "p@ss:w/rd%#?"

SETTINGS_PATH = Path(__file__).resolve().parents[1] / "config" / "settings.yml"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Run from an empty directory so a developer's real .env can't leak into load_config()
    monkeypatch.chdir(tmp_path)
    # Variables already in the environment (CI's job env, or uv's UV_ENV_FILE loading .env)
    # would override the defaults these tests check
    for name in [
        *(f"POSTGRES_{f.upper()}" for f in DatabaseSettings.model_fields),
        "HARVESTER_USER_AGENT",
    ]:
        monkeypatch.delenv(name, raising=False)


def write_settings(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    """Copy the repo's settings.yml with one change applied."""
    data = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "settings.yml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def make_settings(**overrides: object) -> DatabaseSettings:
    # _env_file=None keeps a developer's real .env out of the tests
    return DatabaseSettings(_env_file=None, **{"password": TRICKY_PASSWORD, **overrides})  # type: ignore[arg-type]


def test_url_round_trips_special_characters_in_password() -> None:
    rendered = make_settings().url.render_as_string(hide_password=False)

    parsed = make_url(rendered)

    assert parsed.password == TRICKY_PASSWORD
    assert parsed.database == "iris"
    assert parsed.drivername == "postgresql+psycopg"


def test_password_masked_in_str_and_repr() -> None:
    settings = make_settings()

    assert TRICKY_PASSWORD not in str(settings.url)
    assert TRICKY_PASSWORD not in repr(settings)


def test_test_url_targets_test_database() -> None:
    settings = make_settings(host="db.internal", port=6543, test_db="iris_ci")

    assert settings.test_url.database == "iris_ci"
    assert settings.test_url.host == "db.internal"
    assert settings.test_url.port == 6543
    assert settings.url.database == "iris"


def test_missing_password_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    with pytest.raises(ValidationError, match="password"):
        DatabaseSettings(_env_file=None)


def test_repo_settings_load() -> None:
    config = load_config(SETTINGS_PATH)

    sachsen = config.sources[0]
    assert isinstance(sachsen, SachsenSource)
    assert sachsen.city_slugs == ["dresden", "leipzig"]
    leipzig = config.sources[1]
    assert isinstance(leipzig, LeipzigStadtSource)
    assert leipzig.status_filter_id == 252
    assert config.scraping.timeout_seconds == 30.0
    assert config.bounding_boxes["dresden"].min_lon == 13.55
    assert "Gewerbe" in config.industrial_filter.keywords
    assert config.scraping.user_agent == "IRIS-SIG-08-Harvester/1.0 (bot)"


def test_unknown_key_rejected(tmp_path: Path) -> None:
    path = write_settings(tmp_path, lambda d: d["scraping"].update(request_delay_secs=1))

    with pytest.raises(ValidationError, match="request_delay_secs"):
        load_config(path)


def test_inverted_bounding_box_rejected(tmp_path: Path) -> None:
    def swap_lon(d: dict[str, Any]) -> None:
        d["bounding_boxes"]["dresden"] = [13.95, 50.95, 13.55, 51.18]

    with pytest.raises(ValidationError, match="min must be below max"):
        load_config(write_settings(tmp_path, swap_lon))


def test_city_without_bounding_box_rejected(tmp_path: Path) -> None:
    path = write_settings(tmp_path, lambda d: d["bounding_boxes"].pop("leipzig"))

    with pytest.raises(ValidationError, match="no bounding box for: leipzig"):
        load_config(path)


def test_blank_keyword_rejected(tmp_path: Path) -> None:
    path = write_settings(tmp_path, lambda d: d["industrial_filter"]["keywords"].append("  "))

    with pytest.raises(ValidationError, match="keywords"):
        load_config(path)


def test_user_agent_overridden_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARVESTER_USER_AGENT", "IRIS-SIG-08-Harvester/1.0 (bot; ops@example.org)")

    config = load_config(SETTINGS_PATH)

    assert config.scraping.user_agent == "IRIS-SIG-08-Harvester/1.0 (bot; ops@example.org)"
