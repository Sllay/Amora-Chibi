# Web Fish turn streaming — test matrix

- Fish PCM chunks preserve order and sample alignment.
- Provider fallback may happen before first Fish audio only.
- Core stream endpoint is authenticated and does not mutate delivery state by fetching alone.
- Web state advertises stream capability without secrets.
- Safari client uses `ReadableStream` + `PcmStreamPlayer`, not `arrayBuffer()` on the normal cloud path.
- WAV compatibility remains available.
- One Web cognitive turn produces one voice lease while internal speech segments remain persisted.
- `started` reflects actual player start; `ACK spoken=true` reflects actual player completion.
- stop/mute/barge-in abort transport/player and preserve text.
- cloud provider audio remains passthrough (`DSP none`).
- expressive tags derive from Core emotion/cues and never alter display text.
