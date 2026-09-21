import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.api.schema_validation import validate_output, validate_schema
from app.domain.errors import GatewayError
from app.domain.models import JSONSchema

IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
VERSION = re.compile(r"^v[1-9][0-9]{0,9}$")
SECRET_OR_PERSONAL = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"\bAKIA[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{16,}\b|\b[0-9]{16}\b",
    re.IGNORECASE,
)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class DatasetCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    difficulty: Literal["easy", "ambiguous", "malformed", "long_context", "adversarial"]
    task: Literal["extraction", "classification", "generation"]
    input: str = Field(min_length=1, max_length=100_000)
    input_padding_spaces: int = Field(default=0, ge=0, le=4096)
    schema_: JSONSchema | None = Field(default=None, alias="schema")
    expected: JsonValue = None
    expected_outcome: Literal["success", "failure"]
    labels: list[str] | None = None
    rubric: str | None = Field(default=None, max_length=500)

    @property
    def resolved_input(self) -> str:
        return self.input + " " * self.input_padding_spaces

    @model_validator(mode="after")
    def validate_contract(self) -> "DatasetCase":
        if self.task == "extraction":
            if self.schema_ is None or self.labels is not None or self.rubric is not None:
                raise ValueError("Extraction requires only a schema and expected result")
            try:
                validate_schema(self.schema_)
                if self.expected_outcome == "success":
                    validate_output(json.dumps(self.expected), self.schema_)
            except GatewayError as error:
                raise ValueError("Invalid extraction schema or expected result") from error
            if self.expected_outcome == "failure" and self.expected is not None:
                raise ValueError("Failed cases cannot claim an expected valid output")
        elif self.task == "classification":
            if (
                self.schema_ is not None
                or self.rubric is not None
                or not self.labels
                or len(set(self.labels)) != len(self.labels)
                or any(not IDENTIFIER.fullmatch(label) for label in self.labels)
                or self.expected not in self.labels
            ):
                raise ValueError("Classification requires one expected approved label")
        elif (
            self.schema_ is not None
            or self.labels is not None
            or self.expected is not None
            or not self.rubric
        ):
            raise ValueError("Generation requires a documented human-review rubric")
        return self


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str
    provenance: str = Field(min_length=1, max_length=200)
    approved: Literal[True]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: list[DatasetCase] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_manifest(self) -> "DatasetManifest":
        if not IDENTIFIER.fullmatch(self.name) or not VERSION.fullmatch(self.version):
            raise ValueError("Invalid dataset identity")
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case ID")
        raw_cases = [
            case.model_dump(mode="json", by_alias=True, exclude_defaults=True)
            for case in self.cases
        ]
        # The exact on-disk JSON is checked separately, including explicit nulls and defaults.
        if SECRET_OR_PERSONAL.search(canonical(raw_cases).decode()):
            raise ValueError("Dataset resembles personal or secret data")
        return self


def load_dataset(name: str, version: str, *, root: Path = Path("datasets")) -> DatasetManifest:
    if not IDENTIFIER.fullmatch(name) or not VERSION.fullmatch(version):
        raise ValueError("Invalid dataset identity")
    path = root / f"{name}-{version}.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 262_144:
        raise ValueError("Approved dataset manifest is unavailable or exceeds size limit")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate dataset field")
            result[key] = value
        return result

    raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise ValueError("Invalid dataset manifest")
    manifest = DatasetManifest.model_validate(raw)
    if manifest.name != name or manifest.version != version:
        raise ValueError("Dataset identity mismatch")
    if hashlib.sha256(canonical(raw["cases"])).hexdigest() != manifest.content_sha256:
        raise ValueError("Dataset content hash mismatch")
    return manifest


def case_hash(case: DatasetCase) -> str:
    return hashlib.sha256(canonical(case.model_dump(mode="json", by_alias=True))).hexdigest()
