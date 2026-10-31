# Inventário HTTP — modo cloud

Gerado da composição real da API em 2026-10-31. O gate ASGI protege rotas
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
| GET | `/v1/avatar/bundle` | admin ou avatar |
| PUT | `/v1/avatar/bundle` | admin ou avatar |
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
| GET | `/v1/operations/state` | admin ou operator |
| GET | `/v1/operator/bindings/export` | admin ou operator |
| POST | `/v1/operator/bindings/import/apply` | admin ou operator |
| POST | `/v1/operator/bindings/import/preview` | admin ou operator |
| GET | `/v1/operator/people` | admin ou operator |
| GET | `/v1/operator/people/export` | admin ou operator |
| POST | `/v1/operator/people/import/apply` | admin ou operator |
| POST | `/v1/operator/people/import/preview` | admin ou operator |
| GET | `/v1/operator/people/{entity_id}/bindings` | admin ou operator |
| POST | `/v1/operator/people/{entity_id}/bindings` | admin ou operator |
| GET | `/v1/operator/people/{entity_id}/bindings/{binding_ref}` | admin ou operator |
| PUT | `/v1/operator/people/{entity_id}/bindings/{binding_ref}` | admin ou operator |
| DELETE | `/v1/operator/people/{entity_id}/bindings/{binding_ref}` | admin ou operator |
| PUT | `/v1/operator/people/{entity_id}` | admin ou operator |
| GET | `/v1/operator/relationships` | admin ou operator |
| PUT | `/v1/operator/relationships` | admin ou operator |
| DELETE | `/v1/operator/relationships` | admin ou operator |
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
| GET | `/v1/web/voice/utterances/{utterance_id}/stream` | admin ou web (WEB_SAFARI) |
| GET | `/v1/world/history` | admin |
| POST | `/v1/world/record` | admin |
| GET | `/v1/world/state` | admin |
| POST | `/v1/world/{claim_id}/promote` | admin |

`/web/**` e `/avatar/**`: GET/HEAD públicos, assets sem estado privado.
`/docs`, `/redoc`, `/openapi.json`: desabilitados.
`/v1/events/stream`: WebSocket administrativo; Bearer no header, origem validada.
Worker separado: `/live` público; `/ready` e POST `/v1/render` exigem credencial de serviço.

## Browser roles

- `web`: conversa/voz somente no contrato `WEB_SAFARI`;
- `avatar`: superfície Avatar Studio existente;
- `operator`: somente `/v1/operator/**` e `GET /v1/operations/state`;
- `admin`: superfície interna completa.

`operator` não acessa `/v1/web/*`, `/v1/state`, memória, mundo, cognition journal, events ou ActionEngine. `web` não acessa `/v1/operator/*` nem `/v1/operations/state`.

O `client=operator` de `/v1/pairing/redeem` consome um challenge separado criado por `amora pair --role operator`; um código comum de `amora pair` não pode ser promovido mudando o campo `client`.
