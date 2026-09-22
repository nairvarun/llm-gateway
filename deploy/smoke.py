import json
import os
import ssl
import time
import urllib.error
import urllib.request
from http.client import HTTPException
from pathlib import Path


def tls_context() -> ssl.SSLContext | None:
    ca_file = os.environ.get("GATEWAY_SMOKE_CA_FILE")
    return ssl.create_default_context(cafile=ca_file) if ca_file else None


def wait_ready(url: str, context: ssl.SSLContext | None = None) -> None:
    deadline = time.monotonic() + 30
    while True:
        try:
            with urllib.request.urlopen(
                url + "/health/ready", timeout=2, context=context
            ) as response:
                assert response.status == 200
            break
        except (urllib.error.URLError, OSError, HTTPException):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Gateway did not become ready; inspect local startup logs."
                ) from None
            time.sleep(0.5)


def main() -> None:
    url = os.environ.get("GATEWAY_SMOKE_URL", "http://127.0.0.1:8000")
    key = Path(os.environ.get("GATEWAY_SMOKE_KEY_FILE", ".local/client-key")).read_text().strip()
    context = tls_context()
    wait_ready(url, context)
    for endpoint, payload in (
        ("generate", {"input": "synthetic demo"}),
        ("extract", {"input": '{"count": 2}', "schema_name": "demo-count", "schema_version": "v1"}),
    ):
        request = urllib.request.Request(
            url + "/v1/" + endpoint,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-API-Key": key},
        )
        with urllib.request.urlopen(request, timeout=5, context=context) as response:
            body = json.load(response)
            assert response.status == 200
            assert body["request_id"] == response.headers["X-Request-ID"]
            assert body["estimated_cost_usd"] == "0"
            if endpoint == "extract":
                assert body["output"] == {"count": 2}
            else:
                assert body["output"] == "Mock response: synthetic demo"
            print(f"{endpoint}: passed; request_id={body['request_id']}")


if __name__ == "__main__":
    main()
