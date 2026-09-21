import json
from pathlib import Path

from app.evaluation.datasets import load_dataset
from app.evaluation.revision import code_revision
from deploy.benchmark import percentile, summarize


def test_checked_in_benchmark_evidence_reaggregates() -> None:
    evidence = json.loads(
        (Path(__file__).resolve().parents[1] / "docs/benchmark-evidence-v1.json").read_text()
    )
    assert evidence["dataset"] == "synthetic-gateway@v1"
    assert evidence["dataset_sha256"] == load_dataset("synthetic-gateway", "v1").content_sha256
    assert evidence["code_revision_sha256"] == code_revision()
    assert evidence["case_count"] == 7
    assert evidence["repetitions"] == 2
    assert evidence["concurrency"] == 1
    report = (Path(__file__).resolve().parents[1] / "docs/milestone-5-benchmark.md").read_text()
    for condition in evidence["conditions"].values():
        rows = condition["cases"]
        assert len(rows) == 14
        assert all("input" not in row and "output" not in row for row in rows)
        assert condition["summary"] == summarize(rows)
        p95 = condition["summary"]["p95_end_to_end_ms"]
        assert f"{p95:.3f} ms" in report
        assert {(row["case_id"], row["repetition"]) for row in rows} == {
            (row["case_id"], repetition) for row in rows[:7] for repetition in (1, 2)
        }


def test_p95_uses_nearest_rank_including_failures() -> None:
    assert percentile([float(value) for value in range(1, 21)], 0.95) == 19.0
    assert percentile([], 0.95) == 0.0
