# Offline provider selection (task 2.1)

Checked against official vendor model and SDK pages on 20 September 2026.
This is a design-time price snapshot, not a provider invoice or guarantee of
account availability. No provider credentials, calls, or quality benchmarks were
used. Exact SDK pins are in `pyproject.toml` and `uv.lock`.

| Provider/model ID | SDK | Text/JSON capability | Advertised context/output ceilings | Published base text price per 1M input/output tokens | Conservative bound status |
| --- | --- | --- | --- | --- | --- |
| OpenAI `gpt-5.6-luna` | `openai==3.16.2` | Text generation, structured outputs through Responses API | 1,050,000 / 128,000 tokens | USD 0.20 / 1.20 | Token-count upper bound not yet established; strict-budget dispatch disabled |
| Anthropic `claude-haiku-4-5-20251001` | `anthropic==1.7.0` | Text generation, structured outputs through Messages API | 200,000 / 64,000 tokens | USD 1.00 / 5.00 | Token-count upper bound not yet established; strict-budget dispatch disabled |

Both profiles are text-only in this gateway, even where the vendor supports
other modalities. The public gateway caps output at 16,384 tokens, below both
vendor maxima. The context/output figures are vendor limits, **not** proof that
an arbitrary prompt fits or that a per-request upper token estimate exists.
The milestone 2 router marks both live profiles unrouteable because no
adapter-specific conservative method covers prompt wrappers and schemas yet.
The synthetic mock alone uses a UTF-8-byte bound plus a 1,024-token local
margin. Live token bounds and price tiers must be revalidated before the
milestone 4 live-dispatch gate; the price matrix is not an executable allowance.

OpenAI's published price has a long-input tier: prompts above 272,000 input
tokens bill the full request at 2× input and 1.5× output. Its cache-write and
cached-input prices also differ from the base rate. The later immutable pricing
record must model the applicable tier rather than using USD 0.20/1.20 for every
request. Anthropic's quoted base prices likewise exclude cache/batch-specific
rates. No cache discount may be assumed in a conservative reservation.

The OpenAI ID is an alias on its model page, with no separate dated snapshot
listed. Its upstream behavior can drift. Anthropic's selected ID is dated. All
evaluations must record the actual returned model ID and SDK version; neither
selection claims reproducibility of live outputs. The models' quality/latency
labels are vendor descriptions, not measured gateway rankings.

Fixture provenance: `tests/fixtures/providers/{openai,anthropic}/success.json`
contains small, hand-authored **synthetic** success responses based on the
vendors' published response structures. They are not recorded traffic,
customer data, or actual model outputs. `tests/test_provider_fixtures.py`
parses them with the pinned public SDK response types and checks key fields.
Fault variants are derived from those synthetic base fixtures in
`tests/test_openai_adapter.py`, `tests/test_anthropic_adapter.py`, and the shared
`tests/test_live_provider_contract.py`. The latter runs the same normalization
and classified-failure cases across both SDK adapters and the mock. Both SDKs
use an in-memory transport, so no test calls a vendor endpoint. Wire-shape drift
must fail the offline tests after an SDK update; review this document before
changing pins.

Source pages:

- [OpenAI GPT-5.6 Luna model, limits, features, and prices](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [OpenAI Python SDK release and retry configuration](https://github.com/openai/openai-python/releases/tag/v3.16.2)
- [Anthropic model comparison and Haiku ID/limits/prices](https://platform.claude.com/docs/en/models/overview)
- [Anthropic structured-output compatibility and refusal/length caveats](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Anthropic Python SDK release](https://github.com/anthropics/anthropic-sdk-python/releases/tag/v1.7.0)
- [Anthropic API retry behavior](https://platform.claude.com/docs/en/api/errors)
