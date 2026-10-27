import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from harvester.config import DatabaseSettings

# Characters that break a hand-written postgresql:// URL unless percent-encoded
TRICKY_PASSWORD = "p@ss:w/rd%#?"


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
