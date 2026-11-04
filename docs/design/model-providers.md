# Model providers

> Source: `coworker/providers/` — `base.py` (ABC + value types),
> `router.py` (`ProviderRouter`), `registry.py` (descriptors + builders),
> plus the native provider modules.

## The abstraction

`ProviderClient` (`base.py:102`) is a small ABC — `complete` / `stream` /
`capabilities` — operating on a **canonical OpenAI-shaped message format**.
Everything above the provider layer (engine, persistence, transcripts) only
ever sees that shape, plus the value types `AssistantTurn`, `ToolCall`,
`TokenUsage`, `StreamChunk`.

`ProviderRouter` (`router.py:23`) routes by model-string prefix
(`anthropic/claude-…`, `openai/gpt-…`), and `registry.py` declares
`ProviderDescriptor`s: the UI-facing form (title, API-key fields, endpoint
prefill, env fallback key) plus a `build` function producing a
`ProviderClient`.

## Native SDK clients

Not everything goes through one OpenAI-compat shim. Native clients exist
where the provider has capabilities the compat shape can't carry:

| Module | Provider | Why native |
|---|---|---|
| `anthropic_provider.py` | Anthropic | Claude Messages API; thinking blocks ride an `_anthropic` message sidecar |
| `gemini_provider.py` | Google | google-genai; thought signatures ride a `_gemini` sidecar |
| `bedrock_provider.py` | AWS Bedrock | boto3 (optional dependency, lazy-imported) |
| `vertex_provider.py` | GCP Vertex | service-account / ADC / MaaS-bearer credentials |
| `openai_responses.py` | OpenAI | native routes to `/v1/responses` — chat.completions rejects tools+reasoning on GPT-5.6 |
| `codex_provider.py` | OpenAI Codex | Codex-specific auth |

## OpenAI-compat vendors

`openai_provider.py` is the compat workhorse (also Ollama). Registered
vendors (`registry.py`): `ark` (BytePlus), `ark-agent-plan-cn` (Volcengine),
`zai` (Z AI / GLM), `deepseek`, `kimi` (Moonshot), `minimax`, `qwen`
(Alibaba), `xai` (Grok), `mistral`, `meta` (Muse Spark), `together`,
`fireworks`, `openrouter`, `ollama` (local, endpoint field instead of key).
Each descriptor prefills the vendor's official endpoint; the field stays
editable for regional/proxy variants.

## Sidecars and model switching

The canonical message list may carry private keys — `_anthropic` (thinking
blocks), `_gemini` (thought signatures), `_display` (GUI-only). The engine's
`_outbound_messages()` strips everything except what the current provider
understands before each request. This one rule buys a headline feature:
**you can switch models mid-session** — history stays valid because no
provider's private format ever leaked into the shared transcript.

## Where aisuite actually sits

Despite the README's "built on aisuite", aisuite is **not** the LLM transport
here. It is used for:

- tool-schema generation (`aisuite.utils.tools.Tools` in
  `tools/registry.py`) and `ToolMetadata` on tool functions;
- prebuilt toolkits (`aisuite.toolkits` files/git in `catalog.py`) — some of
  which OpenWorker replaces with its own faster variants.

Provider transport, streaming, permissions, and the agent loop are all owned
by this repo. The pinned commit in `pyproject.toml` is the import point from
the original aisuite monorepo.

## PDF support

`coworker/pdf_support.py` adapts PDF attachments for models without native
PDF input: pypdf for text extraction, pypdfium2 for page rasterization —
deliberately *not* PyMuPDF, whose AGPL license can't ride in the DMG (see
`pyproject.toml`).
