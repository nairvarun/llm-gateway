import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.datasets import DatasetManifest, canonical, case_hash, load_dataset


def test_synthetic_manifest_is_approved_versioned_and_strict() -> None:
    manifest = load_dataset("synthetic-gateway", "v1")
    assert manifest.approved is True
    assert len(manifest.cases) == 7
    assert {case.difficulty for case in manifest.cases} == {
        "easy",
        "ambiguous",
        "malformed",
        "long_context",
        "adversarial",
    }
    assert {case.task for case in manifest.cases} == {
        "extraction",
        "classification",
        "generation",
    }
    assert len({case_hash(case) for case in manifest.cases}) == len(manifest.cases)
    assert len(manifest.cases[4].resolved_input) > 2000


def test_tampering_invalidates_content_hash(tmp_path: Path) -> None:
    source = json.loads(Path("datasets/synthetic-gateway-v1.json").read_text())
    source["cases"][0]["input"] = "changed"
    (tmp_path / "synthetic-gateway-v1.json").write_text(json.dumps(source))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_dataset("synthetic-gateway", "v1", root=tmp_path)


def test_schema_and_privacy_checks_reject_bad_fixture(tmp_path: Path) -> None:
    source = json.loads(Path("datasets/synthetic-gateway-v1.json").read_text())
    source["cases"][0]["expected"] = {"count": "wrong type"}
    source["content_sha256"] = hashlib.sha256(canonical(source["cases"])).hexdigest()
    (tmp_path / "synthetic-gateway-v1.json").write_text(json.dumps(source))
    with pytest.raises(ValidationError):
        load_dataset("synthetic-gateway", "v1", root=tmp_path)
    source["cases"][0]["expected"] = {"count": 2}
    source["cases"][0]["input"] = "person@example.com"
    source["content_sha256"] = hashlib.sha256(canonical(source["cases"])).hexdigest()
    (tmp_path / "synthetic-gateway-v1.json").write_text(json.dumps(source))
    with pytest.raises(ValidationError, match="personal or secret"):
        load_dataset("synthetic-gateway", "v1", root=tmp_path)


def test_path_traversal_duplicate_keys_and_identity_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="identity"):
        load_dataset("../synthetic-gateway", "v1")
    (tmp_path / "synthetic-gateway-v1.json").write_text('{"name":"one","name":"two"}')
    with pytest.raises(ValueError, match="Duplicate"):
        load_dataset("synthetic-gateway", "v1", root=tmp_path)
    source = json.loads(Path("datasets/synthetic-gateway-v1.json").read_text())
    source["cases"].append(source["cases"][0])
    source["content_sha256"] = hashlib.sha256(canonical(source["cases"])).hexdigest()
    (tmp_path / "synthetic-gateway-v1.json").write_text(json.dumps(source))
    with pytest.raises(ValidationError, match="Duplicate case"):
        load_dataset("synthetic-gateway", "v1", root=tmp_path)


def test_unapproved_manifest_is_invalid() -> None:
    source = json.loads(Path("datasets/synthetic-gateway-v1.json").read_text())
    source["approved"] = False
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(source)
