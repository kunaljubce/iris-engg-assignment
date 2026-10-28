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


class _StrictModel(BaseModel):
    # A misspelt key would otherwise be ignored silently; e.g. a typo in `keywords` would
    # leave the filter empty and the run would "succeed" with zero matches
    model_config = ConfigDict(extra="forbid")


class City(_StrictModel):
    slug: str
    label: str


class SachsenSource(_StrictModel):
    type: Literal["sachsen"]
    name: str
    base_url: str
    cities: list[City]
    format_filter: str
    # Literal: a misspelt status would select nothing and silently skip those plans
    status_filters: list[Literal["AKTUELLE", "BEENDETE"]]

    @property
    def city_slugs(self) -> list[str]:
        return [city.slug for city in self.cities]


class ScrapingConfig(_StrictModel):
    request_delay_seconds: float
    max_retries: int
    retry_backoff_factor: float
    user_agent: str
    timeout_seconds: float


class IndustrialFilterConfig(_StrictModel):
    keywords: list[NonEmptyStr] = Field(min_length=1)
    case_sensitive_tokens: list[NonEmptyStr]


class BoundingBox(_StrictModel):
    """WGS84 box, written in YAML as [min_lon, min_lat, max_lon, max_lat]."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

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
        # A swapped box contains no point, so every record would be rejected_out_of_bounds
        if self.min_lon >= self.max_lon or self.min_lat >= self.max_lat:
            raise ValueError("min must be below max (are lon/lat or the corners swapped?)")
        return self


class HarvesterConfig(_StrictModel):
    sources: list[SachsenSource]
    scraping: ScrapingConfig
    industrial_filter: IndustrialFilterConfig
    bounding_boxes: dict[str, BoundingBox]

    @model_validator(mode="after")
    def _every_city_has_a_bounding_box(self) -> Self:
        # Fail now rather than with a KeyError while geocoding, after the whole scrape
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
