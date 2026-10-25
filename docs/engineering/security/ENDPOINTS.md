# Inventário HTTP — modo cloud

Gerado da composição real da API em 2026-10-23. O gate ASGI protege rotas
antes da leitura do body, independentemente de Depends. WebSocket e mounts
estáticos são descritos após a tabela.

| Método | Rota | Credencial |
|---|---|---|
| GET | `/health` | public |
| GET | `/live` | public |
| GET | `/ready` | public |
| POST | `/v1/actions/execute` | admin |
| GET | `/v1/actions/executions` | admin |
| POST | `/v1/actions/plan` | admin |
| GET | `/v1/actions/plans` | admin |
| GET | `/v1/actions/state` | admin |
| POST | `/v1/audio/transcribe` | admin ou web (WEB_SAFARI) |
| POST | `/v1/autonomy/cycle` | admin |
| GET | `/v1/autonomy/decisions` | admin |
| POST | `/v1/autonomy/drives/{drive}/adjust` | admin |
| GET | `/v1/autonomy/state` | admin |
| GET | `/v1/avatar/assets` | admin ou avatar |
| DELETE | `/v1/avatar/assets/{asset_id}` | admin ou avatar |
| GET | `/v1/avatar/assets/{asset_id}` | admin ou avatar |
| PUT | `/v1/avatar/assets/{asset_id}` | admin ou avatar |
| GET | `/v1/avatar/project` | admin ou avatar |
| PUT | `/v1/avatar/project` | admin ou avatar |
| GET | `/v1/avatar/state` | admin ou avatar |
| GET | `/v1/cognition/history` | admin |
| POST | `/v1/cognition/interrupt/{turn_id}` | admin |
| GET | `/v1/cognition/journal` | admin |
| GET | `/v1/cognition/journal/{turn_id}` | admin |
| POST | `/v1/cognition/replay/{turn_id}` | admin |
| POST | `/v1/cognition/respond` | admin |
| GET | `/v1/cognition/segments/{turn_id}` | admin |
| GET | `/v1/cognition/state` | admin |
| GET | `/v1/cognition/usage` | admin |
| GET | `/v1/conversation/next` | admin ou web (WEB_SAFARI) |
| GET | `/v1/conversation/state` | admin |
| POST | `/v1/conversation/test-turn` | admin |
| POST | `/v1/conversation/turn` | admin |
| GET | `/v1/conversation/turns` | admin |
| GET | `/v1/conversation/utterances` | admin |
| POST | `/v1/conversation/utterances/{utterance_id}/ack` | admin ou web (WEB_SAFARI) |
| POST | `/v1/conversation/utterances/{utterance_id}/cancel` | admin ou web (WEB_SAFARI) |
| POST | `/v1/conversation/utterances/{utterance_id}/started` | admin ou web (WEB_SAFARI) |
| GET | `/v1/emotion/affect/{entity_id}` | admin |
| POST | `/v1/emotion/appraise` | admin |
| GET | `/v1/emotion/bids` | admin |
| POST | `/v1/emotion/bids/{bid_id}/consume` | admin |
| GET | `/v1/emotion/events` | admin |
| GET | `/v1/emotion/state` | admin |
| POST | `/v1/events` | admin |
| POST | `/v1/language/addressee` | admin |
| POST | `/v1/language/observe` | admin |
| POST | `/v1/language/resolve` | admin |
| POST | `/v1/memory/recall` | admin |
| POST | `/v1/memory/remember` | admin |
| GET | `/v1/memory/{memory_id}` | admin |
| POST | `/v1/memory/{memory_id}/forget` | admin |
| GET | `/v1/operations/state` | admin |
| POST | `/v1/pairing/redeem` | public |
| GET | `/v1/relationships/history` | admin |
| GET | `/v1/self` | admin |
| POST | `/v1/sessions` | admin |
| DELETE | `/v1/sessions/{session_id}` | admin |
| GET | `/v1/state` | admin |
| GET | `/v1/voice/profiles` | admin |
| GET | `/v1/voice/state` | admin |
| GET | `/v1/voice/utterances/{utterance_id}/audio` | admin |
| GET | `/v1/web/state` | admin ou web (WEB_SAFARI) |
| POST | `/v1/web/utterance` | admin ou web (WEB_SAFARI) |
| GET | `/v1/web/voice/utterances/{utterance_id}/audio` | admin ou web (WEB_SAFARI) |
| GET | `/v1/world/history` | admin |
| POST | `/v1/world/record` | admin |
| GET | `/v1/world/state` | admin |
| POST | `/v1/world/{claim_id}/promote` | admin |

`/web/**` e `/avatar/**`: GET/HEAD públicos, assets sem estado privado.
`/docs`, `/redoc`, `/openapi.json`: desabilitados.
`/v1/events/stream`: WebSocket administrativo; Bearer no header, origem validada.
Worker separado: `/live` público; `/ready` e POST `/v1/render` exigem credencial de serviço.
As credenciais de browser não acessam /operations, eventos brutos, journal, memória, mundo ou tools administrativas.
