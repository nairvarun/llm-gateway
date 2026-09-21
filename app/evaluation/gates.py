from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.evaluation.profiles import ThresholdProfile


class GateResult(BaseModel):
    status: str
    baseline_run_id: UUID | None
    reasons: list[str]
    deltas: dict[str, str]


def compare(
    current: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    profile: ThresholdProfile,
    baseline_run_id: UUID | None,
) -> GateResult:
    if baseline is None or baseline_run_id is None:
        return GateResult(
            status="blocked", baseline_run_id=None, reasons=["missing_approved_baseline"], deltas={}
        )
    if current is None:
        return GateResult(
            status="blocked",
            baseline_run_id=baseline_run_id,
            reasons=["missing_run_evidence"],
            deltas={},
        )
    for evidence in (current, baseline):
        measurements = evidence.get("measurements", {})
        generation = evidence.get("generation", {})
        if (
            not measurements.get("cost_evidence_complete")
            or not measurements.get("latency_evidence_complete")
            or measurements.get("unknown_usage_attempts")
            or Decimal(str(measurements.get("held_liability_usd", "0"))) != 0
            or generation.get("human_review_pending", 0)
            or generation.get("human_review_accepted", 0) != generation.get("total_cases", 0)
        ):
            return GateResult(
                status="blocked",
                baseline_run_id=baseline_run_id,
                reasons=["incomplete_accounting_or_human_review"],
                deltas={},
            )

    def metric(report: dict[str, Any], section: str, key: str) -> Decimal:
        value = report.get(section, {}).get(key)
        if value is None:
            raise ValueError("Required evaluation metric is unavailable")
        try:
            return Decimal(str(value))
        except InvalidOperation as error:
            raise ValueError("Required evaluation metric is invalid") from error

    try:
        validity = metric(current, "extraction", "schema_validity")
        accuracy = metric(current, "extraction", "field_accuracy")
        f1 = metric(current, "classification", "macro_f1")
        latency = metric(current, "measurements", "p95_latency_ms")
        cost = metric(current, "measurements", "estimated_fresh_cost_usd")
        baseline_accuracy = metric(baseline, "extraction", "field_accuracy")
        baseline_f1 = metric(baseline, "classification", "macro_f1")
        baseline_latency = metric(baseline, "measurements", "p95_latency_ms")
        baseline_cost = metric(baseline, "measurements", "estimated_fresh_cost_usd")
    except ValueError:
        return GateResult(
            status="blocked",
            baseline_run_id=baseline_run_id,
            reasons=["missing_required_metric"],
            deltas={},
        )
    limits = profile.parameters
    reasons = []
    if validity < Decimal(str(limits["minimum_extraction_validity"])):
        reasons.append("extraction_validity_below_threshold")
    if accuracy < Decimal(str(limits["minimum_extraction_field_accuracy"])):
        reasons.append("extraction_accuracy_below_threshold")
    if f1 < Decimal(str(limits["minimum_classification_f1"])):
        reasons.append("classification_f1_below_threshold")
    if accuracy < baseline_accuracy - Decimal(str(limits["maximum_accuracy_drop"])):
        reasons.append("extraction_regressed_vs_baseline")
    if f1 < baseline_f1 - Decimal(str(limits["maximum_accuracy_drop"])):
        reasons.append("classification_regressed_vs_baseline")
    if latency > Decimal(str(limits["maximum_p95_latency_ms"])):
        reasons.append("latency_exceeded")
    if cost > Decimal(str(limits["maximum_estimated_cost_usd"])) or cost > baseline_cost * Decimal(
        str(limits["maximum_cost_ratio_to_baseline"])
    ):
        reasons.append("cost_exceeded")
    return GateResult(
        status="failed" if reasons else "passed",
        baseline_run_id=baseline_run_id,
        reasons=reasons,
        deltas={
            "extraction_field_accuracy": str(accuracy - baseline_accuracy),
            "classification_macro_f1": str(f1 - baseline_f1),
            "p95_latency_ms": str(latency - baseline_latency),
            "estimated_fresh_cost_usd": str(cost - baseline_cost),
        },
    )
