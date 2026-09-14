import hashlib
import json
import math
from typing import cast
from urllib.parse import unquote

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.domain.errors import GatewayError
from app.domain.models import JSONSchema, JSONValue

DIALECT = "https://json-schema.org/draft/2020-12/schema"
KEYWORDS = frozenset(
    {
        "$schema",
        "$ref",
        "$defs",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
        "title",
        "description",
        "default",
        "examples",
    }
)


def invalid_schema() -> GatewayError:
    return GatewayError("INVALID_SCHEMA", "Schema is invalid, unsupported, or exceeds limits.", 422)


def validate_schema(schema: JSONSchema, *, max_bytes: int = 32_768, max_depth: int = 16) -> str:
    try:
        encoded = json.dumps(
            schema, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (ValueError, TypeError, RecursionError) as error:
        raise invalid_schema() from error
    if len(encoded) > max_bytes:
        raise invalid_schema()
    visits = 0

    def resolve(ref: str) -> JSONValue:
        if not ref.startswith("#") or (ref != "#" and not ref.startswith("#/")):
            raise invalid_schema()
        node: JSONValue = schema
        if ref == "#":
            return node
        for token in unquote(ref[2:]).split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or token not in node:
                raise invalid_schema()
            node = node[token]
        return node

    def walk(node: JSONValue, depth: int, ancestors: frozenset[int]) -> None:
        nonlocal visits
        visits += 1
        if visits > 1024 or depth > max_depth:
            raise invalid_schema()
        if isinstance(node, bool):
            return
        if not isinstance(node, dict) or id(node) in ancestors or set(node) - KEYWORDS:
            raise invalid_schema()
        if "$schema" in node and node["$schema"] != DIALECT:
            raise invalid_schema()
        ancestors = ancestors | {id(node)}
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str):
                raise invalid_schema()
            walk(resolve(ref), depth + 1, ancestors)
        for keyword in ("properties", "$defs"):
            children = node.get(keyword)
            if children is not None:
                if not isinstance(children, dict):
                    raise invalid_schema()
                for child in children.values():
                    walk(child, depth + 1, ancestors)
        for keyword in ("items", "additionalProperties"):
            if keyword in node:
                walk(node[keyword], depth + 1, ancestors)

    walk(schema, 1, frozenset())
    try:
        Draft202012Validator.check_schema(schema)
    except (SchemaError, ValueError, RecursionError) as error:
        raise invalid_schema() from error
    return hashlib.sha256(encoded).hexdigest()


def validate_output(output: str, schema: JSONSchema) -> JSONValue:
    def reject_constant(value: str) -> None:
        raise ValueError("Non-finite JSON number")

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite JSON number")
        return number

    def unique_object(pairs: list[tuple[str, JSONValue]]) -> dict[str, JSONValue]:
        result: dict[str, JSONValue] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def bounded(value: JSONValue, depth: int = 0) -> int:
        if depth > 32:
            raise ValueError("JSON depth exceeded")
        children = (
            value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
        )
        count = 1
        for child in children:
            count += bounded(child, depth + 1)
            if count > 4096:
                raise ValueError("JSON size exceeded")
        return count

    try:
        parsed = cast(
            JSONValue,
            json.loads(
                output,
                parse_constant=reject_constant,
                parse_float=finite_float,
                object_pairs_hook=unique_object,
            ),
        )
        bounded(parsed)
        Draft202012Validator(schema).validate(parsed)
        return parsed
    except (ValueError, TypeError, RecursionError, ValidationError) as error:
        raise GatewayError(
            "OUTPUT_VALIDATION_FAILED", "Output did not satisfy the schema.", 502
        ) from error
