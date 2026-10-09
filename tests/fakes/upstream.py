"""A fake OpenAI + Anthropic server whose next responses are scripted by each test.

Script steps:
    chunks(n, delay=0)          send n content chunks, `delay` seconds apart
    wait(s)                     sleep (a first-byte or idle stall)
    status(code, retry_after)   respond with an HTTP error (must be the first step)
    usage(input, output)        token counts reported at the end
    drop()                      close the connection mid-stream

The fake records every request body and every stream the client hung up on.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any


def chunks(n: int, delay: float = 0) -> tuple:
    return ("chunks", n, delay)


def wait(s: float) -> tuple:
    return ("wait", s)


def status(code: int, retry_after: float | None = None) -> tuple:
    return ("status", code, retry_after)


def usage(input_tokens: int, output_tokens: int) -> tuple:
    return ("usage", input_tokens, output_tokens)


def drop() -> tuple:
    return ("drop",)


class _Drop(Exception):
    pass


class FakeUpstream:
    def __init__(self) -> None:
        self.url = ""
        self.scripts: deque[list[tuple]] = deque()
        self.requests: list[dict[str, Any]] = []
        self.disconnects = 0

    def script(self, *steps: tuple) -> None:
        self.scripts.append(list(steps))

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        raw = b""
        while True:
            msg = await receive()
            raw += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        req = json.loads(raw or b"{}")
        path = scope["path"]
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        self.requests.append({"path": path, "body": req, "headers": headers})
        steps = self.scripts.popleft() if self.scripts else [chunks(3)]
        flavor = "anthropic" if path.endswith("/messages") else "openai"

        if steps and steps[0][0] == "status":
            _, code, retry_after = steps[0]
            extra = [(b"retry-after", str(retry_after).encode())] if retry_after is not None else []
            await self._json(send, code, {"error": {"message": f"fake error {code}"}}, extra)
            return
        if not req.get("stream"):
            await self._complete(send, steps, flavor, req)
            return

        gone = asyncio.Event()

        async def watch() -> None:
            while True:
                if (await receive())["type"] == "http.disconnect":
                    self.disconnects += 1
                    gone.set()
                    return

        watcher = asyncio.create_task(watch())
        try:
            await self._stream(send, steps, flavor, req, gone)
        except _Drop:
            return  # returning without finishing the body makes the server cut the connection
        finally:
            watcher.cancel()

    async def _json(self, send, code: int, obj: dict, extra: list | None = None) -> None:
        body = json.dumps(obj).encode()
        headers = [(b"content-type", b"application/json")] + (extra or [])
        await send({"type": "http.response.start", "status": code, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def _complete(self, send, steps: list[tuple], flavor: str, req: dict) -> None:
        n, (tin, tout) = 0, (10, 0)
        for step in steps:
            if step[0] == "wait":
                await asyncio.sleep(step[1])
            elif step[0] == "chunks":
                n += step[1]
            elif step[0] == "usage":
                tin, tout = step[1], step[2]
        text = "".join(f"tok{i} " for i in range(n))
        tout = tout or n
        if flavor == "openai":
            obj = {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": req.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": tin,
                    "completion_tokens": tout,
                    "total_tokens": tin + tout,
                },
            }
        else:
            obj = {
                "id": "msg_fake",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": tin, "output_tokens": tout},
            }
        await self._json(send, 200, obj)

    async def _stream(
        self, send, steps: list[tuple], flavor: str, req: dict, gone: asyncio.Event
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )

        async def event(obj: dict, name: str | None = None) -> None:
            head = f"event: {name}\n".encode() if name else b""
            body = head + b"data: " + json.dumps(obj).encode() + b"\n\n"
            await send({"type": "http.response.body", "body": body, "more_body": True})

        n, started, tin, tout = 0, False, 10, None
        for step in steps:
            if step[0] == "usage":
                tin, tout = step[1], step[2]
        for step in steps:
            if step[0] == "wait":
                try:  # stop waiting as soon as the client hangs up
                    await asyncio.wait_for(gone.wait(), step[1])
                    raise _Drop
                except TimeoutError:
                    pass
            elif step[0] == "drop":
                raise _Drop
            elif step[0] == "chunks":
                for _ in range(step[1]):
                    if step[2]:
                        await asyncio.sleep(step[2])
                    if flavor == "anthropic" and not started:
                        started = True
                        msg = {"id": "msg_fake", "usage": {"input_tokens": tin}}
                        await event({"type": "message_start", "message": msg}, "message_start")
                    text = f"tok{n} "
                    n += 1
                    if flavor == "openai":
                        await event(
                            {
                                "id": "chatcmpl-fake",
                                "object": "chat.completion.chunk",
                                "created": 0,
                                "model": req.get("model"),
                                "choices": [
                                    {"index": 0, "delta": {"content": text}, "finish_reason": None}
                                ],
                            }
                        )
                    else:
                        delta = {"type": "text_delta", "text": text}
                        await event(
                            {"type": "content_block_delta", "index": 0, "delta": delta},
                            "content_block_delta",
                        )
        tout = n if tout is None else tout
        if flavor == "openai":
            if (req.get("stream_options") or {}).get("include_usage"):
                u = {"prompt_tokens": tin, "completion_tokens": tout, "total_tokens": tin + tout}
                await event(
                    {
                        "id": "chatcmpl-fake",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": req.get("model"),
                        "choices": [],
                        "usage": u,
                    }
                )
            await send(
                {"type": "http.response.body", "body": b"data: [DONE]\n\n", "more_body": True}
            )
        else:
            if not started:
                msg = {"id": "msg_fake", "usage": {"input_tokens": tin}}
                await event({"type": "message_start", "message": msg}, "message_start")
            await event(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": tout},
                },
                "message_delta",
            )
            await event({"type": "message_stop"}, "message_stop")
        await send({"type": "http.response.body", "body": b"", "more_body": False})
