"""Runtime configuration.

Two sources, split by whether a value is secret:

- Database settings come from the same POSTGRES_* variables that docker-compose.yml uses, so the
  password lives in exactly one place (.env). The SQLAlchemy URL is assembled here rather than
  written by hand, which percent-encodes special characters in the password.
- Harvester behaviour (sources, scraping, filter keywords, bounding boxes) comes from
  config/settings.yml, validated once at startup so a typo fails before any page is fetched.

Both paths are relative to the current directory: run from the repository root.
"""

from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

DEFAULT_CONFIG_PATH = Path("config/settings.yml")


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="POSTGRES_",
        env_file=".env",
        # .env also holds non-database variables (e.g. HARVESTER_USER_AGENT)
        extra="ignore",
    )

    host: str = "localhost"
    port: int = 5432
    user: str = "iris"
    # SecretStr keeps the password out of repr() and logs
    password: SecretStr
    db: str = "iris"
    test_db: str = "iris_test"

    def _url(self, database: str) -> URL:
        # URL.create escapes the password itself; str(URL) masks it as ***
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=database,
        )

    @property
    def url(self) -> URL:
        """URL of the harvester database."""
        return self._url(self.db)

    @property
    def test_url(self) -> URL:
        """URL of the separate database used by the db-marked tests."""
        return self._url(self.test_db)


# An empty keyword would match every title, so blank entries are rejected
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
HttpsUrl = Annotated[str, StringConstraints(pattern=r"^https://")]


class _StrictModel(BaseModel):
    # An unknown key is almost always a typo; fail instead of silently using a default
    model_config = ConfigDict(extra="forbid")


class City(_StrictModel):
    slug: NonEmptyStr
    label: NonEmptyStr


class SachsenSource(_StrictModel):
    type: Literal["sachsen"]
    name: NonEmptyStr
    base_url: HttpsUrl
    cities: list[City] = Field(min_length=1)
    format_filter: NonEmptyStr
    status_filters: list[Literal["AKTUELLE", "BEENDETE"]] = Field(min_length=1)

    @property
    def city_slugs(self) -> list[str]:
        return [city.slug for city in self.cities]


class LeipzigStadtSource(_StrictModel):
    type: Literal["leipzig_stadt"]
    name: NonEmptyStr
    base_url: HttpsUrl
    listing_path: Annotated[str, StringConstraints(pattern=r"^/")]

    @property
    def city_slugs(self) -> list[str]:
        # The city portal only covers Leipzig
        return ["leipzig"]


# `type` picks the model, so an error message names the fields of the right source kind
Source = Annotated[SachsenSource | LeipzigStadtSource, Field(discriminator="type")]


class ScrapingConfig(_StrictModel):
    headless: bool
    request_delay_seconds: float = Field(ge=0)
    max_retries: int = Field(ge=0)
    # Below 1 the wait between retries would shrink instead of grow
    retry_backoff_factor: float = Field(ge=1)
    user_agent: NonEmptyStr
    page_timeout_ms: int = Field(gt=0)


class IndustrialFilterConfig(_StrictModel):
    keywords: list[NonEmptyStr] = Field(min_length=1)
    case_sensitive_tokens: list[NonEmptyStr]
    thema_signals: list[NonEmptyStr]


class BoundingBox(_StrictModel):
    """WGS84 box, written in YAML as [min_lon, min_lat, max_lon, max_lat]."""

    min_lon: float = Field(ge=-180, le=180)
    min_lat: float = Field(ge=-90, le=90)
    max_lon: float = Field(ge=-180, le=180)
    max_lat: float = Field(ge=-90, le=90)

    @model_validator(mode="before")
    @classmethod
    def _from_list(cls, data: Any) -> Any:
        if isinstance(data, list | tuple):
            if len(data) != 4:
                raise ValueError("expected [min_lon, min_lat, max_lon, max_lat]")
            return dict(zip(("min_lon", "min_lat", "max_lon", "max_lat"), data, strict=True))
        return data

    @model_validator(mode="after")
    def _min_below_max(self) -> Self:
        if self.min_lon >= self.max_lon or self.min_lat >= self.max_lat:
            raise ValueError("min must be below max (are lon/lat or the corners swapped?)")
        return self


class HarvesterConfig(_StrictModel):
    sources: list[Source] = Field(min_length=1)
    scraping: ScrapingConfig
    industrial_filter: IndustrialFilterConfig
    bounding_boxes: dict[str, BoundingBox]

    @model_validator(mode="after")
    def _every_city_has_a_bounding_box(self) -> Self:
        # Without a box, geocoding a city's records could not reject bad coordinates
        cities = {slug for source in self.sources for slug in source.city_slugs}
        missing = sorted(cities - self.bounding_boxes.keys())
        if missing:
            raise ValueError(f"no bounding box for: {', '.join(missing)}")
        return self


class _EnvOverrides(BaseSettings):
    """Optional overrides, read from the environment or .env like DatabaseSettings."""

    model_config = SettingsConfigDict(env_prefix="HARVESTER_", env_file=".env", extra="ignore")

    # Lets an operator add a contact address without committing it to settings.yml
    user_agent: str | None = None


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> HarvesterConfig:
    """Load and validate settings.yml, then apply HARVESTER_USER_AGENT if set."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    config = HarvesterConfig.model_validate(data)

    overrides = _EnvOverrides()
    if overrides.user_agent:
        config.scraping.user_agent = overrides.user_agent
    return config
