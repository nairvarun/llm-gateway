import pytest

from app.api.schema_validation import validate_output, validate_schema
from app.domain.errors import GatewayError
from app.domain.models import JSONSchema


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.com/schema"},
        {"$ref": "relative.json"},
        {"$ref": "#"},
        {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"},
        {"type": "not-a-type"},
        {"type": "string", "pattern": ".*"},
        {"allOf": [{"type": "string"}]},
        {"$schema": "wrong"},
    ],
)
def test_reject_invalid_remote_recursive_and_unsupported(schema: JSONSchema) -> None:
    with pytest.raises(GatewayError) as error:
        validate_schema(schema)
    assert error.value.code == "INVALID_SCHEMA"


def test_local_references_and_hash_stability() -> None:
    schema: JSONSchema = {"$defs": {"number": {"type": "integer"}}, "$ref": "#/$defs/number"}
    digest = validate_schema(schema)
    assert digest == validate_schema(dict(reversed(list(schema.items()))))
    assert validate_output("3", schema) == 3


def test_size_depth_and_expansion_limits() -> None:
    with pytest.raises(GatewayError):
        validate_schema({"description": "x" * 100}, max_bytes=64)
    schema: JSONSchema = {"type": "object", "properties": {"x": {"type": "string"}}}
    with pytest.raises(GatewayError):
        validate_schema(schema, max_depth=1)


@pytest.mark.parametrize("output", ["{invalid", '{"x": 1}', '{"x": "a", "x": "b"}', '{"x": NaN}'])
def test_invalid_outputs_are_sanitized(output: str) -> None:
    schema: JSONSchema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    }
    with pytest.raises(GatewayError) as error:
        validate_output(output, schema)
    assert error.value.code == "OUTPUT_VALIDATION_FAILED"
    assert output not in error.value.message


def test_overflowing_json_number_is_not_success() -> None:
    with pytest.raises(GatewayError):
        validate_output("1e999999", {"type": "number"})
