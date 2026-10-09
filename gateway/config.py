"""Configuration: secrets and knobs from the environment, behavior from one YAML file.

Everything is validated at startup; any error stops the process before it serves traffic.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    gateway_config: Path = Path("/etc/gateway/config.yaml")
    gateway_db_path: str = "/data/gateway.db"
    admin_token: str = Field(min_length=32)
    otel_exporter_otlp_endpoint: str | None = None
    log_level: str = "INFO"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProviderCfg(_Frozen):
    base_url: str
    api_key_env: str
    default_max_tokens: int = Field(1024, gt=0)  # used by the Anthropic adapter


class TargetCfg(_Frozen):
    provider: str
    model: str


class ModelCfg(_Frozen):
    targets: list[TargetCfg] = Field(min_length=1)


class Price(_Frozen):
    input: float = Field(ge=0)  # USD per 1M tokens
    output: float = Field(ge=0)


class Toggle(_Frozen):
    enabled: bool = True


class CacheCfg(_Frozen):
    enabled: bool = True
    max_entries: int = Field(1000, gt=0)
    ttl_s: float = Field(300, gt=0)


class StagesCfg(_Frozen):
    cache: CacheCfg = CacheCfg()
    budget: Toggle = Toggle()


class Timeouts(_Frozen):
    connect_s: float = Field(3, gt=0)
    first_byte_s: float = Field(20, gt=0)
    idle_s: float = Field(30, gt=0)
    total_s: float = Field(300, gt=0)


class Retries(_Frozen):
    max_attempts: int = Field(3, ge=1, le=5)
    backoff_base_s: float = Field(0.25, ge=0)


class BreakerCfg(_Frozen):
    failure_threshold: int = Field(5, ge=1)
    open_s: float = Field(30, gt=0)


class ShutdownCfg(_Frozen):
    drain_s: float = Field(330, gt=0)
    drain_delay_s: float = Field(10, ge=0)  # how long /internal/drain waits before answering


class Config(_Frozen):
    providers: dict[Literal["openai", "anthropic"], ProviderCfg]
    models: dict[str, ModelCfg]
    pricing: dict[str, Price]
    stages: StagesCfg = StagesCfg()
    timeouts: Timeouts = Timeouts()
    retries: Retries = Retries()
    breaker: BreakerCfg = BreakerCfg()
    shutdown: ShutdownCfg = ShutdownCfg()

    @model_validator(mode="after")
    def cross_checks(self) -> Config:
        for alias, model in self.models.items():
            if not ALIAS_RE.match(alias):
                raise ValueError(f"models.{alias}: alias must match {ALIAS_RE.pattern}")
            for i, t in enumerate(model.targets):
                path = f"models.{alias}.targets[{i}]"
                if t.provider not in self.providers:
                    raise ValueError(f"{path}.provider {t.provider!r} is not in providers")
                if t.model not in self.pricing:
                    raise ValueError(f"{path}.model {t.model!r} has no entry in pricing")
        t = self.timeouts
        if t.first_byte_s > t.total_s or t.idle_s > t.total_s:
            raise ValueError("timeouts: first_byte_s and idle_s must not exceed total_s")
        if self.shutdown.drain_s < t.total_s:
            raise ValueError("shutdown.drain_s must be at least timeouts.total_s")
        return self


class ConfigError(ValueError):
    pass


def load_config(path: Path, environ: Mapping[str, str] | None = None) -> Config:
    environ = os.environ if environ is None else environ
    data = yaml.safe_load(Path(path).read_text())
    cfg = Config.model_validate(data)
    for name, p in cfg.providers.items():
        if not environ.get(p.api_key_env):
            raise ConfigError(f"providers.{name}.api_key_env: ${p.api_key_env} is not set")
    return cfg
