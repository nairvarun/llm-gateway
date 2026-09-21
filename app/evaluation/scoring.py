from collections import Counter
from decimal import Decimal
from hashlib import sha256
from typing import Any

from app.evaluation.datasets import DatasetCase

SCORER_VERSION = "offline-scorers-v1"
GENERATION_RUBRIC_VERSION = "human-rubric-v1"


def score_case(case: DatasetCase, output: object | None, error_code: str | None) -> dict[str, Any]:
    """Return only bounded, non-content evidence; failures remain scored cases."""
    succeeded = error_code is None and output is not None
    common: dict[str, Any] = {
        "scorer_version": SCORER_VERSION,
        "task": case.task,
        "difficulty": case.difficulty,
        "execution_succeeded": succeeded,
        "expected_outcome_matched": succeeded == (case.expected_outcome == "success"),
    }
    if case.task == "extraction":
        expected = case.expected if isinstance(case.expected, dict) else {}
        observed = output if succeeded and isinstance(output, dict) else {}
        fields_correct = sum(
            observed.get(key, object()) == value for key, value in expected.items()
        )
        return {
            **common,
            "schema_valid": succeeded,
            "fields_correct": fields_correct,
            "fields_expected": len(expected),
            "case_exact_match": succeeded and output == case.expected,
        }
    if case.task == "classification":
        prediction = output.strip() if succeeded and isinstance(output, str) else None
        prediction = prediction if prediction in (case.labels or []) else None
        return {
            **common,
            "prediction": prediction,  # approved bounded label, never raw model text
            "expected_label": case.expected,
            "correct": prediction == case.expected,
        }
    return {
        **common,
        "rubric_version": GENERATION_RUBRIC_VERSION,
        "rubric": case.rubric,
        "human_review_status": "pending" if succeeded else "not_applicable",
        "output_sha256": sha256(output.encode()).hexdigest()
        if succeeded and isinstance(output, str)
        else None,
        "human_review_credential_id": None,
        "human_review_at": None,
    }


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    """Publish explicit denominators, including failures and timed-out cases."""
    tasks: dict[str, list[dict[str, Any]]] = {}
    for score in scores:
        tasks.setdefault(str(score["task"]), []).append(score)
    report: dict[str, Any] = {
        "total_cases": len(scores),
        "execution_successes": sum(bool(score["execution_succeeded"]) for score in scores),
    }
    extraction = tasks.get("extraction", [])
    if extraction:
        correct = sum(int(score["fields_correct"]) for score in extraction)
        expected = sum(int(score["fields_expected"]) for score in extraction)
        report["extraction"] = {
            "total_cases": len(extraction),
            "schema_valid_cases": sum(bool(score["schema_valid"]) for score in extraction),
            "exact_match_cases": sum(bool(score["case_exact_match"]) for score in extraction),
            "fields_correct": correct,
            "fields_expected": expected,
            "field_accuracy": str(Decimal(correct) / expected) if expected else None,
            "schema_validity": str(Decimal(report_valid(extraction)) / len(extraction)),
        }
    classification = tasks.get("classification", [])
    if classification:
        label_counts: dict[str, Counter[str]] = {}
        for score in classification:
            expected_label = str(score["expected_label"])
            prediction = score["prediction"]
            label_counts.setdefault(expected_label, Counter())["support"] += 1
            if prediction == expected_label:
                label_counts[expected_label]["true_positive"] += 1
            elif prediction is not None:
                label_counts.setdefault(str(prediction), Counter())["false_positive"] += 1
        by_label: dict[str, dict[str, str | int]] = {}
        for label, counts in sorted(label_counts.items()):
            tp, support, fp = (
                counts["true_positive"],
                counts["support"],
                counts["false_positive"],
            )
            precision = Decimal(tp) / (tp + fp) if tp + fp else Decimal(0)
            recall = Decimal(tp) / support if support else Decimal(0)
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else Decimal(0)
            by_label[label] = {
                "support": support,
                "precision": str(precision),
                "recall": str(recall),
                "f1": str(f1),
            }
        report["classification"] = {
            "total_cases": len(classification),
            "correct_cases": sum(bool(score["correct"]) for score in classification),
            "labels": by_label,
            "macro_f1": str(
                sum(Decimal(str(value["f1"])) for value in by_label.values()) / len(by_label)
            ),
        }
    generation = tasks.get("generation", [])
    if generation:
        report["generation"] = {
            "total_cases": len(generation),
            "human_review_pending": sum(
                score["human_review_status"] == "pending" for score in generation
            ),
            "human_review_accepted": sum(
                score["human_review_status"] == "accepted" for score in generation
            ),
        }
    return report


def report_valid(extraction: list[dict[str, Any]]) -> int:
    return sum(bool(score["schema_valid"]) for score in extraction)
