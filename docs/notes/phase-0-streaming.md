# Phase 0: streaming core proxy

**In two sentences.** The gateway relays a provider's SSE stream chunk by chunk through async
generators, so the first token reaches the client as soon as the provider sends it. Every stage
that needs the final result wraps the generator and acts in `finally`, which runs once whether
the stream completed, failed or was cancelled.

## What I learned

- **Peek before you commit.** Route waits for the first chunk before returning a response.
  Until then no bytes have gone out, so a failure can still become a retry or a real 502/504.
  After it, the status is 200 forever, and errors can only travel as an SSE error event.
- **Disconnect detection depends on the ASGI spec version.** Starlette 1.7 only cancels the
  streaming task on `http.disconnect` when the server reports ASGI spec < 2.4 (uvicorn says 2.3
  today). With 2.4 it would just stop at the next failed `send()`, which never happens while
  the upstream is silent. `SSEResponse` always runs its own disconnect watcher, so the behavior
  does not hinge on a version string.
- **anyio cancellation is level-triggered.** A cancelled scope cancels *every* await until the
  scope exits, so cleanup awaits in `finally` get cancelled too. All cleanup goes through
  `shielded()`, which runs it as a separate task that the cancellation cannot reach.
- **Nested generators do not close themselves.** Breaking out of `async for` leaves the inner
  generator suspended until garbage collection. `wrap_stream` closes its source explicitly
  before its own `on_close`, which is what guarantees "innermost cleanup first".
- **Format translation is mostly about the ends of the stream.** Anthropic sends usage in
  `message_start` (input) and `message_delta` (output); OpenAI only sends it when asked
  (`stream_options.include_usage`). Both adapters end with the same usage-only chunk, so route
  handles one shape.
- **Test streaming over real sockets.** httpx's `ASGITransport` buffers the whole response, which
  would make "first chunk before the upstream finishes" pass or fail for the wrong reason.

## Metric

`gateway_ttft_seconds`: time to first byte, the latency that matters for streaming.
