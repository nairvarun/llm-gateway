import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.gates import compare
from app.evaluation.profiles import load_profile


def report() -> dict[str, object]:
    return {
        "extraction": {"schema_validity": "0.8", "field_accuracy": "0.9"},
        "classification": {"macro_f1": "1"},
        "generation": {"total_cases": 1, "human_review_pending": 0, "human_review_accepted": 1},
        "measurements": {
            "cost_evidence_complete": True,
            "latency_evidence_complete": True,
            "unknown_usage_attempts": 0,
            "held_liability_usd": "0",
            "p95_latency_ms": "100",
            "estimated_fresh_cost_usd": "0.10",
        },
    }


def test_missing_baseline_and_evidence_block() -> None:
    from uuid import uuid4

    profile = load_profile("synthetic-contract@v1")
    assert compare(report(), None, profile, None).reasons == ["missing_approved_baseline"]
    incomplete = report()
    incomplete["measurements"]["cost_evidence_complete"] = False  # type: ignore[index]
    assert compare(incomplete, report(), profile, uuid4()).status == "blocked"


def test_quality_improvement_does_not_override_cost_regression() -> None:
    from uuid import uuid4

    current = report()
    baseline = deepcopy(current)
    current["extraction"]["field_accuracy"] = "0.95"  # type: ignore[index]
    current["measurements"]["estimated_fresh_cost_usd"] = "0.15"  # type: ignore[index]
    result = compare(current, baseline, load_profile("synthetic-contract@v1"), uuid4())
    assert result.status == "failed"
    assert result.reasons == ["cost_exceeded"]
    assert result.deltas["extraction_field_accuracy"] == "0.05"


def test_missing_metric_and_human_review_block() -> None:
    from uuid import uuid4

    current = report()
    current["generation"]["human_review_pending"] = 1  # type: ignore[index]
    assert (
        compare(current, report(), load_profile("synthetic-contract@v1"), uuid4()).status
        == "blocked"
    )
    current = report()
    del current["classification"]
    assert compare(current, report(), load_profile("synthetic-contract@v1"), uuid4()).reasons == [
        "missing_required_metric"
    ]


def test_profile_version_hash_and_bounds_are_immutable(tmp_path: Path) -> None:
    source = json.loads(Path("datasets/profiles/synthetic-contract-v1.json").read_text())
    source["parameters"]["maximum_estimated_cost_usd"] = "2.00"
    (tmp_path / "synthetic-contract-v1.json").write_text(json.dumps(source))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_profile("synthetic-contract@v1", root=tmp_path)
    source["parameters"]["maximum_p95_latency_ms"] = "-1"
    (tmp_path / "synthetic-contract-v1.json").write_text(json.dumps(source))
    with pytest.raises(ValidationError):
        load_profile("synthetic-contract@v1", root=tmp_path)
