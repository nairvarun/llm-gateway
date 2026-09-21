import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.datasets import canonical

Signal = Literal[
    "failure_rate",
    "p95_latency_ms",
    "daily_used_fraction",
    "validation_failure_rate",
    "open_circuits",
    "exporter_failures",
    "remaining_budget_fraction",
]


class AlertRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    signal: Signal
    operator: Literal["ge", "le"]
    threshold: Decimal = Field(ge=0)
    window_seconds: int = Field(ge=30, le=3600)
    runbook: str = Field(pattern=r"^docs/operations/alerts\.md#[a-z-]+$")


class AlertManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["v1"]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rules: list[AlertRule] = Field(min_length=1, max_length=32)


def load_alerts(path: Path = Path("deploy/alert-rules-v1.json")) -> AlertManifest:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
        raise ValueError("Alert rules are unavailable")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Invalid alert rules")
    manifest = AlertManifest.model_validate(raw)
    if len({rule.name for rule in manifest.rules}) != len(manifest.rules):
        raise ValueError("Duplicate alert name")
    digest = hashlib.sha256(
        canonical({key: value for key, value in raw.items() if key != "content_sha256"})
    ).hexdigest()
    if digest != manifest.content_sha256:
        raise ValueError("Alert rules hash mismatch")
    return manifest


def evaluate_alerts(
    manifest: AlertManifest,
    signals: dict[str, Decimal],
    observation_seconds: int,
    *,
    ends_at: datetime | None = None,
) -> dict[str, object]:
    if not 0 < observation_seconds <= 3600:
        raise ValueError("Invalid observation window")
    if set(signals) - {rule.signal for rule in manifest.rules}:
        raise ValueError("Unknown alert signal")
    if any(not value.is_finite() or value < 0 for value in signals.values()):
        raise ValueError("Alert signals must be nonnegative finite decimals")
    end = ends_at or datetime.now(UTC)
    if end.tzinfo is None:
        raise ValueError("Alert window must be timezone aware")
    triggered: list[dict[str, str]] = []
    missing: list[str] = []
    for rule in manifest.rules:
        if observation_seconds < rule.window_seconds or rule.signal not in signals:
            missing.append(rule.name)
            continue
        measured = signals[rule.signal]
        if (rule.operator == "ge" and measured >= rule.threshold) or (
            rule.operator == "le" and measured <= rule.threshold
        ):
            triggered.append(
                {
                    "name": rule.name,
                    "signal": rule.signal,
                    "observed": str(measured),
                    "threshold": str(rule.threshold),
                    "runbook": rule.runbook,
                }
            )
    return {
        "rule_version": manifest.version,
        "rule_sha256": manifest.content_sha256,
        "starts_at": (end - timedelta(seconds=observation_seconds)).isoformat(),
        "ends_at": end.isoformat(),
        "triggered": triggered,
        "missing_evidence": missing,
    }
