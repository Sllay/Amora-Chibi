# Amora Web / Safari Lab V1

Status: **experimental / primary rapid-iteration surface**  
Decision: ADR-0013

This lab exists to stop paying an APK build/install cycle for every small conversation, VAD, barge-in or UI experiment.

It is a **platform adapter** over the existing Amora Core. It does not move identity, memory, emotion, relationships or DialoguePlan into JavaScript.

## What V1 already does

- opens as a mobile-first Safari web client under `/web/`;
- pairs through the existing 6-digit Core pairing flow;
- text input uses the same Language/Social -> Conversation -> Cognition pipeline;
- microphone capture uses Web Audio PCM;
- browser VAD closes user turns and submits WAV through `/v1/audio/transcribe`;
- speech while Amora is talking triggers immediate browser-side barge-in;
- `Parar fala` is an explicit failsafe;
- up to three speech segments are queued ahead in the browser transport;
- shows simple turn latency, inter-speech gap, queue and mic diagnostics;
- development turns default to `test_mode=true` for text input so probes do not automatically consolidate memory/relationship/emotion state.

## Important voice warning

The first Safari lab uses `speechSynthesis` only as a **temporary transport voice**.

It is useful to test:

- spoken wording;
- response length;
- segment transitions;
- interruption;
- barge-in;
- turn timing;
- model/personality changes.

It is **not** the canonical Amora voice and must not be used to judge timbre.

FINAL_B + VoiceSignature V1 Soft remain canonical. The next voice-specific Web milestone is a Voice Worker that outputs canonical audio to a continuous browser queue.

## One-time Termux setup

The Core may stay on localhost. Safari microphone access should use HTTPS, so the fastest development bridge is a Cloudflare Quick Tunnel.

```bash
pkg update
pkg install cloudflared curl
```

Cloudflare Quick Tunnels are for development/testing. They create a temporary `*.trycloudflare.com` HTTPS URL and do not require a Cloudflare account.

## Every test session

### Terminal 1 — Amora Core

```bash
source ~/.venvs/amora/bin/activate
cd ~/amora-src
python -m pip install -e .
amora serve --port 8765
```

### Terminal 2 — HTTPS bridge

```bash
cd ~/amora-src
bash tools/web/start-safari-lab.sh
```

The script prints/inherits the HTTPS URL from `cloudflared`.

If the tunnel prints something like:

```text
https://random-words.trycloudflare.com
```

open on the iPhone:

```text
https://random-words.trycloudflare.com/web/
```

### Optional tunnel protection

Current `cloudflared` supports e-mail-protected Quick Tunnels. For a private test URL:

```bash
export AMORA_ALLOWED_MAIL="seu-email@exemplo.com"
bash tools/web/start-safari-lab.sh
```

The browser will receive a PIN challenge before reaching the Core.

## Pair the Safari client

In another Termux command/session:

```bash
source ~/.venvs/amora/bin/activate
cd ~/amora-src
amora pair
```

Enter the 6-digit code in Safari.

The resulting Core bearer token is stored only in that browser origin's `localStorage`.

Because a Quick Tunnel hostname changes when restarted, a new hostname also means a new browser origin and therefore usually requires pairing again. A stable named tunnel/domain can be introduced after the lab loop is validated.

## Recommended first physical test

1. Open the lab in a normal Safari tab first.
2. Pair.
3. Send `Amora, o que tu tá fazendo?` as text.
4. Confirm the reply appears and the Safari debug voice speaks it.
5. Tap `Ativar microfone` and grant microphone permission.
6. Say `Amora, conta uma história curta pra mim`.
7. While she is speaking, say `Amora, para` in a normal voice.
8. Confirm audio stops immediately and the old queued story does not resume.
9. Repeat while watching `Último gap` and `Fila de voz`.
10. Only after the Safari-tab loop is stable, test Add to Home Screen / standalone mode.

## Why Safari tab first

WebKit supports Home Screen web apps, manifests and modern media APIs, but iOS has had Web Audio/media lifecycle regressions specific to standalone web apps. Testing the normal Safari tab first separates Amora bugs from standalone-PWA lifecycle bugs.

If a standalone web app loses audio after background/resume while the Safari tab still works, treat that as a Web platform-adapter issue instead of changing Core cognition or voice identity.

## Architecture

```text
Safari / Web adapter
  |- pairing/auth
  |- Web Audio mic capture
  |- browser VAD + barge-in
  |- WAV upload
  |- debug speech transport
  `- diagnostics
          |
          v
Amora Core
  |- Language/Social
  |- Addressee
  |- Emotion / DialoguePlan
  |- hybrid cognition
  |- Conversation queue
  `- canonical state
```

No JavaScript component is allowed to become the owner of Amora's canonical identity/state.

## Next Web milestones

1. physical Safari V1 gate;
2. continuous canonical Voice Worker output;
3. server->browser event transport without high-frequency polling;
4. stable HTTPS hostname for daily use;
5. Home Screen PWA lifecycle hardening;
6. later, evaluate persistent remote Core separately under its own ADR.

## References

- https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/
- https://webkit.org/blog/17333/webkit-features-in-safari-26-0/
- https://webkit.org/blog/17862/webkit-features-for-safari-26-4/
- https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/
