import secrets
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.providers.mock import SCENARIOS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", extra="ignore")

    provider: Literal["mock"] = "mock"
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://gateway:local-development-only@127.0.0.1:55432/gateway"
    )
    database_schema: str = Field(default="public", pattern=r"^[a-z][a-z0-9_]{0,62}$")
    redis_url: SecretStr | None = None
    tenant_concurrency: int = Field(default=8, ge=1, le=1000)
    provider_concurrency: int = Field(default=16, ge=1, le=1000)
    tenant_rate_per_minute: int = Field(default=60, ge=1, le=100_000)
    provider_rate_per_minute: int = Field(default=300, ge=1, le=100_000)
    circuit_failure_threshold: int = Field(default=5, ge=1, le=1000)
    circuit_window_seconds: int = Field(default=60, ge=1, le=3600)
    circuit_cooldown_seconds: int = Field(default=30, ge=1, le=3600)
    circuit_probe_lease_seconds: int = Field(default=30, ge=1, le=3600)
    mock_scenario: str = "success"
    body_limit_bytes: int = Field(default=262_144, ge=1024, le=262_144)
    input_limit_chars: int = Field(default=100_000, ge=1, le=100_000)
    schema_limit_bytes: int = Field(default=32_768, ge=64, le=32_768)
    schema_max_depth: int = Field(default=16, ge=1, le=16)
    default_request_cost_usd: Decimal = Field(default=Decimal("1"), gt=0)
    input_hash_key: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_hex(32)))
    replay_encryption_key: SecretStr | None = None
    replay_retention_hours: int = Field(default=24, ge=1, le=24)
    cache_encryption_key: SecretStr | None = None
    cache_ttl_seconds: int = Field(default=3600, ge=1, le=3600)
    cache_redis_url: SecretStr | None = None
    metadata_retention_days: int = Field(default=30, ge=1, le=365)
    trace_sample_rate: float = Field(default=0.1, ge=0, le=1)
    otlp_traces_endpoint: SecretStr | None = None

    @field_validator("otlp_traces_endpoint", mode="before")
    @classmethod
    def empty_telemetry_disabled(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("otlp_traces_endpoint")
    @classmethod
    def valid_telemetry_endpoint(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            parsed = urlparse(value.get_secret_value())
            if parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
            ):
                raise ValueError("OTLP traces require HTTPS or localhost HTTP")
            if (
                not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("OTLP traces endpoint must not contain credentials or query")
        return value

    @field_validator("replay_encryption_key", mode="before")
    @classmethod
    def empty_replay_key_disabled(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("cache_encryption_key", "cache_redis_url", mode="before")
    @classmethod
    def empty_cache_setting_disabled(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("Use a postgresql+asyncpg connection URL")
        return value

    @field_validator("mock_scenario")
    @classmethod
    def valid_scenario(cls, value: str) -> str:
        if value not in SCENARIOS:
            raise ValueError("Unsupported mock scenario")
        return value


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        fields = ", ".join(
            sorted(str(item["loc"][0]) for item in error.errors(include_input=False))
        )
        raise ValueError(f"Invalid GATEWAY_ configuration fields: {fields}.") from None
