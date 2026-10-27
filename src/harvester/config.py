"""Runtime configuration.

Database settings come from the same POSTGRES_* variables that docker-compose.yml uses, so the
password lives in exactly one place (.env). The SQLAlchemy URL is assembled here rather than
written by hand, which percent-encodes special characters in the password.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


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
