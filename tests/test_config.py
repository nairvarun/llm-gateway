from pathlib import Path

import pytest

from app.config import load_settings


def test_invalid_config_does_not_expose_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    private_value = "invalid-protocol://synthetic-private-value"
    monkeypatch.setenv("GATEWAY_DATABASE_URL", private_value)
    with pytest.raises(ValueError) as error:
        load_settings()
    assert "database_url" in str(error.value)
    assert private_value not in str(error.value)


def test_live_provider_configuration_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_PROVIDER", "live")
    with pytest.raises(ValueError):
        load_settings()


def test_empty_optional_replay_key_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_REPLAY_ENCRYPTION_KEY", "")
    assert load_settings().replay_encryption_key is None


def test_file_backed_secrets_override_environment_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_url = "postgresql+asyncpg://gateway:file-secret@database.internal/gateway"
    secret_files = {
        "DATABASE_URL": database_url,
        "INPUT_HASH_KEY": "input-hash-secret",
        "REPLAY_ENCRYPTION_KEY": "replay-secret",
        "CACHE_ENCRYPTION_KEY": "cache-secret",
    }
    for setting, value in secret_files.items():
        path = tmp_path / setting.lower()
        path.write_text(f"{value}\n", encoding="utf-8")
        monkeypatch.setenv(f"GATEWAY_{setting}", "ignored-environment-secret")
        monkeypatch.setenv(f"GATEWAY_{setting}_FILE", str(path))

    settings = load_settings()

    assert settings.database_url.get_secret_value() == database_url
    assert settings.input_hash_key.get_secret_value() == "input-hash-secret"
    assert settings.replay_encryption_key is not None
    assert settings.replay_encryption_key.get_secret_value() == "replay-secret"
    assert settings.cache_encryption_key is not None
    assert settings.cache_encryption_key.get_secret_value() == "cache-secret"


def test_secret_file_error_does_not_expose_other_environment_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_value = "postgresql+asyncpg://gateway:private@database.internal/gateway"
    monkeypatch.setenv("GATEWAY_DATABASE_URL", private_value)
    monkeypatch.setenv("GATEWAY_DATABASE_URL_FILE", str(tmp_path / "missing"))

    with pytest.raises(ValueError) as error:
        load_settings()

    assert "configuration" in str(error.value)
    assert private_value not in str(error.value)
