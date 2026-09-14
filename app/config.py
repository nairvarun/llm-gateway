import secrets
from decimal import Decimal
from typing import Literal

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
    mock_scenario: str = "success"
    body_limit_bytes: int = Field(default=262_144, ge=1024, le=262_144)
    input_limit_chars: int = Field(default=100_000, ge=1, le=100_000)
    schema_limit_bytes: int = Field(default=32_768, ge=64, le=32_768)
    schema_max_depth: int = Field(default=16, ge=1, le=16)
    default_request_cost_usd: Decimal = Field(default=Decimal("1"), gt=0)
    input_hash_key: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_hex(32)))

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
