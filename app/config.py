from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_SESSION_SECRET = "dev-session-secret"
_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = Field(default="local", alias="ENVIRONMENT")
    database_url: str = Field(default="sqlite:///./local.db", alias="DATABASE_URL")
    session_secret: SecretStr = Field(default=SecretStr(_DEFAULT_SESSION_SECRET), alias="SESSION_SECRET")
    session_max_age_seconds: int = Field(default=_SESSION_MAX_AGE_SECONDS, alias="SESSION_MAX_AGE_SECONDS")

    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: SecretStr | None = Field(default=SecretStr("admin"), alias="ADMIN_PASSWORD")
    admin_password_hash: str | None = Field(default=None, alias="ADMIN_PASSWORD_HASH")

    geotab_database: str | None = Field(default=None, alias="GEOTAB_DATABASE")
    geotab_username: str | None = Field(default=None, alias="GEOTAB_USERNAME")
    geotab_password: SecretStr | None = Field(default=None, alias="GEOTAB_PASSWORD")
    geotab_api_key: SecretStr | None = Field(default=None, alias="GEOTAB_API_KEY")
    geotab_server: str = Field(default="my.geotab.com", alias="GEOTAB_SERVER")
    geotab_timeout_seconds: int = Field(default=30, alias="GEOTAB_TIMEOUT_SECONDS")

    sync_interval_minutes: int = Field(default=15, alias="SYNC_INTERVAL_MINUTES")
    sync_lookback_hours: int = Field(default=24, alias="SYNC_LOOKBACK_HOURS")
    scheduler_enabled: bool = Field(default=True, alias="SCHEDULER_ENABLED")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("database_url")
    @classmethod
    def normalize_railway_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @field_validator("geotab_timeout_seconds", "session_max_age_seconds")
    @classmethod
    def validate_positive_seconds(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be a positive integer")
        return value

    @model_validator(mode="after")
    def validate_deployment_settings(self) -> "Settings":
        if self.is_production:
            if self.session_secret.get_secret_value() == _DEFAULT_SESSION_SECRET:
                raise ValueError("SESSION_SECRET must be set to a strong random value when ENVIRONMENT=production")
            if not self.admin_password_hash:
                raise ValueError("ADMIN_PASSWORD_HASH is required when ENVIRONMENT=production")
            if self.database_url.startswith("sqlite:"):
                raise ValueError(
                    "DATABASE_URL must point to PostgreSQL when ENVIRONMENT=production; "
                    "link your Railway Postgres service with ${{Postgres.DATABASE_URL}}"
                )

        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_geotab_configured(self) -> bool:
        return all([self.geotab_database, self.geotab_username, self.geotab_password])


def missing_geotab_credentials(settings: Settings) -> list[str]:
    if not settings.scheduler_enabled:
        return []
    return [
        name
        for name, value in (
            ("GEOTAB_DATABASE", settings.geotab_database),
            ("GEOTAB_USERNAME", settings.geotab_username),
            ("GEOTAB_PASSWORD", settings.geotab_password),
        )
        if not value
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
