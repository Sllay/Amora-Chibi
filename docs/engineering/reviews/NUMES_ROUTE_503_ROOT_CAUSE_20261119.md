# Numes 503 & Qwen fallback — 2026-11-19

## Evidence from live Amora Northflank logs

- Numes HTTPS TLS completed and headers arrived in approximately 495 ms.
- HTTP status was 503, with no SSE frames or content. The original code never inspected the Numes `error.metadata.error_type`; it classified every such outcome as generic `ProviderError`.
- The configured model in the failing payload was `dealignai/deepseek-v4.1-flash-uncensored-fp8`. Numes' public free-model catalogue lists **`dealignai/deepseek-v4.1-flash-uncensored-fp8:free`** instead.
- Live payload included `store: false`, SSE, JSON object, `temperature: 0.65`, `max_tokens: 1200` and a compound provider filter: price zero + `data_collection: deny` + `zdr: true` + `require_parameters: true`.
- Per the Numes documentation, inference may return `503 provider_unavailable` for a model ID not routable to the requesting key, a privacy/parameter filter that eliminates every endpoint, an unavailable model endpoint or interrupted provider service. A true free-model quota violation should normally be `429 rate_limited`, but only the observed error type and account activity can distinguish the causes.
- The previous Numes-specific bootstrap **intentionally registered Groq as memory-only** under the social name `groq`, so it could not realize Qwen social dialogue. As a consequence the actual fallback sequence became Numes → Gemini → OpenRouter, contradicting the operator's Numes → Qwen requirement.

## Correction, without modifying historical memories

- Translate the exact old **bare** DeepSeek FP8 model identifier to the advertised `:free` ID, even if the old value still exists as a Northflank environment variable. Never silently select a paid ID.
- Keep the original zero-price, ZDR, denial-of-collection and required-parameter filters intact for actual user conversations. Do **not** weaken the memory privacy boundary merely to clear 503.
- Register Groq Qwen 3.8 27B as social **secondary** after Numes, while preserving the separate Groq GPT-OSS 120B source-only memory-extraction provider and its local acceptance gates; Gemini/OpenRouter remain independent tertiary fallbacks.
- Add `providers numes audit`: account-key-specific GET `/models/{author}/{slug}/endpoints` to check published access, endpoint count and parameter/output declarations, no user text, no model generation and no changes to Amora memory.
- Keep `providers numes probe` for one synthetic SSE JSON test. On 503 the adapter extracts only allowlisted `error.metadata.error_type` labels into logs, never provider messages, keys, prompts or memory content.

## How to distinguish the real failure

1. On the corrected build, run `providers numes audit` as an authorized Amora admin command. An HTTP 404 means this account cannot see the exact model (unknown/unpublished/currency/access); HTTP 200 shows eligible catalogue records. This GET **does not attest ZDR**, since endpoint privacy declarations are dashboard-only. An empty or stale catalogue is not success.
2. If the catalogue has a routable model, run `providers numes probe` once. A synthetic request uses the exact free route and the production privacy and JSON constraints but no private content.
3. On 503 examine the safe `numes_stream_http_error` / `numes_stream_finished` fields and the Numes account's Activity/Compare providers view. `provider_unavailable` requires filtering/health diagnosis; `service_unavailable` may indicate overload; `rate_limited` is the clear limit signal. `Retry-After` is respected by the Core cooldown.
4. If the free endpoint does not declare `response_format`, `max_tokens` and `temperature` compatibility, inspect which **required** parameter prevents routing. A change to strict parameters is a separate privacy-preserving decision; do not drop ZDR/data-collection requirements on actual conversations without operator approval.
5. If Numes still fails, a regular user turn should select Qwen 3.8 27B via Groq **before** Gemini, and the memory specialist continues to operate independently.

## Operator caution

The external catalogue is live and can change, and a public free-model availability percentage is not evidence of endpoint eligibility for this account. This review contains no Numes keys, actual prompts or private memory content. CI uses mock HTTP responses and a synthetic Core; no real Numes inference has yet been executed to certify success.
