import urllib.error
from http.client import RemoteDisconnected
from unittest.mock import MagicMock

import pytest

from deploy.smoke import wait_ready


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("starting"),
        RemoteDisconnected("starting"),
        ConnectionResetError(),
        TimeoutError(),
    ],
)
def test_readiness_wait_tolerates_startup_disconnects(
    failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = MagicMock()
    response.__enter__.return_value.status = 200
    opener = MagicMock(side_effect=[failure, response])
    monkeypatch.setattr("deploy.smoke.urllib.request.urlopen", opener)
    monkeypatch.setattr("deploy.smoke.time.sleep", lambda seconds: None)
    wait_ready("http://127.0.0.1:8000")
    assert opener.call_count == 2


def test_readiness_wait_fails_visibly_after_startup_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = MagicMock(side_effect=RemoteDisconnected("starting"))
    clock = MagicMock(side_effect=[0, 31])
    monkeypatch.setattr("deploy.smoke.urllib.request.urlopen", opener)
    monkeypatch.setattr("deploy.smoke.time.monotonic", clock)
    with pytest.raises(RuntimeError, match="did not become ready"):
        wait_ready("http://127.0.0.1:8000")
