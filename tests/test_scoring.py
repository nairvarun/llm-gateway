from decimal import Decimal

from app.evaluation.datasets import load_dataset
from app.evaluation.scoring import aggregate, score_case


def test_failures_stay_in_extraction_denominator() -> None:
    cases = load_dataset("synthetic-gateway", "v1").cases
    good = score_case(cases[0], {"count": 2}, None)
    invalid = score_case(cases[1], None, "OUTPUT_VALIDATION_FAILED")
    timed_out = score_case(cases[4], None, "DEADLINE_EXCEEDED")
    report = aggregate([good, invalid, timed_out])["extraction"]
    assert report["total_cases"] == 3
    assert report["schema_valid_cases"] == 1
    assert report["schema_validity"] == str(Decimal(1) / 3)
    assert report["fields_correct"] == 1
    assert report["fields_expected"] == 2
    assert report["field_accuracy"] == "0.5"


def test_classification_precision_recall_f1_count_failure() -> None:
    case = load_dataset("synthetic-gateway", "v1").cases[5]
    scores = [
        score_case(case, "positive", None),
        score_case(case, "negative", None),
        score_case(case, None, "DEADLINE_EXCEEDED"),
    ]
    result = aggregate(scores)["classification"]
    assert result["total_cases"] == 3
    assert result["correct_cases"] == 1
    assert result["labels"]["positive"]["recall"] == str(Decimal(1) / 3)
    assert result["labels"]["positive"]["precision"] == "1"
    assert result["labels"]["negative"]["f1"] == "0"
    assert scores[-1]["prediction"] is None


def test_generation_needs_human_review_and_records_no_output() -> None:
    case = load_dataset("synthetic-gateway", "v1").cases[-1]
    score = score_case(case, "Mock response: synthetic greeting", None)
    assert score["human_review_status"] == "pending"
    assert "Mock response" not in str(score)
    assert aggregate([score])["generation"]["human_review_pending"] == 1
