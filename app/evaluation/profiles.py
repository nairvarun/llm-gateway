import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.datasets import canonical

IDENTITY = re.compile(r"^([a-z][a-z0-9-]{0,62})@(v[1-9][0-9]{0,9})$")


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_extraction_validity: Decimal = Field(ge=0, le=1)
    minimum_extraction_field_accuracy: Decimal = Field(ge=0, le=1)
    minimum_classification_f1: Decimal = Field(ge=0, le=1)
    maximum_p95_latency_ms: Decimal = Field(gt=0, le=120_000)
    maximum_estimated_cost_usd: Decimal = Field(gt=0, le=1000)
    maximum_cost_ratio_to_baseline: Decimal = Field(ge=1, le=100)
    maximum_accuracy_drop: Decimal = Field(ge=0, le=1)
    require_human_generation_review: Literal[True]


class ProfileManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str
    dataset_name: str
    dataset_version: str
    approved: Literal[True]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters: Thresholds


@dataclass(frozen=True)
class ThresholdProfile:
    identity: str
    content_sha256: str
    dataset_name: str
    dataset_version: str
    parameters: dict[str, object]


def load_profile(identity: str, *, root: Path = Path("datasets/profiles")) -> ThresholdProfile:
    matched = IDENTITY.fullmatch(identity)
    if matched is None:
        raise ValueError("Invalid threshold profile identity")
    name, version = matched.groups()
    path = root / f"{name}-{version}.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
        raise ValueError("Approved threshold profile is unavailable")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate threshold field")
            result[key] = value
        return result

    raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(raw, dict):
        raise ValueError("Invalid threshold profile")
    manifest = ProfileManifest.model_validate(raw)
    if manifest.name != name or manifest.version != version:
        raise ValueError("Threshold identity mismatch")
    digest = hashlib.sha256(
        canonical({key: val for key, val in raw.items() if key != "content_sha256"})
    ).hexdigest()
    if digest != manifest.content_sha256:
        raise ValueError("Threshold profile hash mismatch")
    return ThresholdProfile(
        identity,
        digest,
        manifest.dataset_name,
        manifest.dataset_version,
        manifest.parameters.model_dump(mode="json"),
    )
