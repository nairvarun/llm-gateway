import json
from pathlib import Path

from app.config import Settings
from app.main import create_app
from tests.fakes import MemoryStore


def test_milestone_four_public_contract_remains_available() -> None:
    baseline = json.loads(Path("docs/api/compatibility-baseline-v4.json").read_text())
    schema = create_app(Settings(), store=MemoryStore()).openapi()
    components = schema["components"]["schemas"]
    for path, entry in baseline["paths"].items():
        assert path in schema["paths"]
        operation = schema["paths"][path][entry["method"]]
        assert entry["success"] in operation["responses"]
        response_ref = operation["responses"][entry["success"]]["content"]["application/json"][
            "schema"
        ]["$ref"]
        assert response_ref.endswith("/" + entry["response"])
        if "request" in entry:
            request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            assert request_ref.endswith("/" + entry["request"])
    for name, entry in baseline["request_schemas"].items():
        current = components[name]
        assert set(current["required"]) <= set(entry["required"])
        assert set(entry["fields"]) <= set(current["properties"])
        for field, values in entry.get("enums", {}).items():
            assert set(values) <= set(current["properties"][field]["enum"])
    for name, fields in baseline["response_schemas"].items():
        assert set(fields) <= set(components[name]["properties"])
