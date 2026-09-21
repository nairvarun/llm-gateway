import json
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from app.observability.alerts import evaluate_alerts, load_alerts


def test_scripted_incident_evidence_and_runbooks() -> None:
    incident = json.loads(Path("tests/fixtures/alerts/incidents.json").read_text())
    evidence = evaluate_alerts(
        load_alerts(),
        {key: Decimal(value) for key, value in incident["signals"].items()},
        incident["observation_seconds"],
    )
    fired = cast(list[dict[str, str]], evidence["triggered"])
    assert [item["name"] for item in fired] == incident["expected"]
    assert all(Path(item["runbook"].split("#")[0]).is_file() for item in fired)
    assert not cast(list[str], evidence["missing_evidence"])


def test_missing_signal_or_short_window_is_not_silently_healthy() -> None:
    evidence = evaluate_alerts(load_alerts(), {"open_circuits": Decimal(0)}, 60)
    assert evidence["triggered"] == []
    assert "high_spend" in cast(list[str], evidence["missing_evidence"])
    assert "open_circuits" not in cast(list[str], evidence["missing_evidence"])


def test_tampered_rules_and_untrusted_signals_are_rejected(tmp_path: Path) -> None:
    raw = json.loads(Path("deploy/alert-rules-v1.json").read_text())
    raw["rules"][0]["threshold"] = "1"
    path = tmp_path / "alerts.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_alerts(path)
    with pytest.raises(ValueError, match="Unknown"):
        evaluate_alerts(load_alerts(), {"tenant_id": Decimal(1)}, 900)
    with pytest.raises(ValueError, match="finite"):
        evaluate_alerts(load_alerts(), {"failure_rate": Decimal("NaN")}, 900)
