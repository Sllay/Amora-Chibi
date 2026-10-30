# Provider notes — Fish streaming

The implementation intentionally starts with Fish HTTP streaming (`POST /v1/tts`, `format=pcm`) rather than a browser/provider WebSocket.

Reasons:

- Fish already streams response bytes over the HTTP endpoint;
- Core can preserve the existing authentication/privacy boundary;
- Safari never receives Fish credentials;
- no extra WebSocket dependency is required in the Core runtime;
- the existing `StreamingVoiceBackend` and `PcmStreamPlayer` contracts can be activated directly;
- a future provider WebSocket remains possible if text generation itself becomes incremental or HTTP proxy buffering proves unacceptable.

`latency=balanced` is deliberate. Physical reports include occasional robotic coloration already; removing full-file wait is preferred over selecting a more aggressive quality/latency trade-off without evidence.
