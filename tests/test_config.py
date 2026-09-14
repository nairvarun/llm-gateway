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
