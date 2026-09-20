import asyncio
import base64
import json
from uuid import UUID

from app.config import Settings
from app.domain.models import ProviderInput, ProviderResult
from app.main import create_app
from app.providers.mock import MockProvider
from tests.fakes import MemoryStore


class HangingProvider(MockProvider):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()

    async def invoke(self, request: ProviderInput) -> ProviderResult:
        self.invocations += 1
        self.entered.set()
        await self.resume.wait()
        return await super().invoke(request)


async def test_client_disconnect_marks_dispatched_attempt_and_key_uncertain() -> None:
    store = MemoryStore()
    provider = HangingProvider()
    settings = Settings(replay_encryption_key=base64.b64encode(bytes(range(32))).decode())
    app = create_app(settings, store=store, provider=provider)
    data = json.dumps({"input": "synthetic", "idempotency_key": "disconnect-key"}).encode()
    incoming: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    await incoming.put({"type": "http.request", "body": data, "more_body": False})
    outgoing: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return await incoming.get()

    async def send(message: dict[str, object]) -> None:
        outgoing.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/generate",
        "raw_path": b"/v1/generate",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"x-api-key", store.key.encode()),
        ],
        "client": ("test", 1),
        "server": ("test", 80),
    }
    execution = asyncio.create_task(app(scope, receive, send))  # type: ignore[arg-type]
    await asyncio.wait_for(provider.entered.wait(), 1)
    await incoming.put({"type": "http.disconnect"})
    await asyncio.wait_for(execution, 1)
    assert provider.invocations == 1
    assert len(store.records) == 1
    request_id = next(iter(store.records))
    assert isinstance(request_id, UUID)
    assert store.records[request_id].status == "uncertain"
    assert store.attempt_outcomes[store.attempts[request_id][0].attempt_id][0] == "uncertain"
    assert next(iter(store.keyed.values()))[2] == "uncertain"
    assert outgoing == []
