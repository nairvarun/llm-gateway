from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from gateway.config import Config, ConfigError, load_config
from tests.conftest import config_dict, make_config

EXAMPLE = Path(__file__).parents[2] / "deploy" / "k8s" / "base" / "config.yaml"


def test_valid_config():
    cfg = make_config()
    assert cfg.models["fast"].targets[1].provider == "anthropic"


def test_missing_pricing_fails_startup():
    data = config_dict()
    del data["pricing"]["claude-test"]
    with pytest.raises(ValidationError, match=r"models\.fast\.targets\[1\]\.model"):
        Config.model_validate(data)


def test_unknown_provider():
    with pytest.raises(ValidationError, match="is not in providers"):
        make_config(models={"x": {"targets": [{"provider": "azure", "model": "gpt-test"}]}})


def test_drain_must_cover_total_timeout():
    with pytest.raises(ValidationError, match="drain_s"):
        make_config(shutdown={"drain_s": 10})


def test_typo_in_key_is_rejected():
    with pytest.raises(ValidationError, match="timout"):
        make_config(timout={"connect_s": 1})


def test_example_config_loads_and_needs_keys():
    env = {"OPENAI_API_KEY": "a", "ANTHROPIC_API_KEY": "b"}
    cfg = load_config(EXAMPLE, env)
    assert set(cfg.models) >= {"fast", "smart"}
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_config(EXAMPLE, {"OPENAI_API_KEY": "a"})
