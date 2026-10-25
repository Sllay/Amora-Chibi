# Incidente Web/Safari + Hrana — 2026-10-25

Base: `main` / `c5b8c4de59a75f34ed4a2272e5448e1c4a5819c9`.
Branch: `fix/cloud-web-runtime`. Não houve deploy pelo agente.

## Evidência e limite da conclusão

BK relata bootstrap/restart e probes saudáveis no Northflank, mas erros por
request `DatabaseError/REQUEST_FAILED` em texto e STT, apesar de HTTP 200 no
Turso. Não há código SQLite nem traceback sanitizado completo fornecido ainda.
O ambiente de desenvolvimento não possui credenciais/URLs de staging em suas
bindings ou variáveis AMORA. Portanto **o incidente real permanece aberto**:
não se certificou Northflank/Turso/Safari nem áudio remoto nesta revisão.

Foi reproduzida uma causa concreta compatível, executando SQL real em SQLite
através de serialização Hrana e múltiplas conexões/transações reais, com o wire
retornando códigos primários de constraint. Antes da correção, o payload mínimo
`Oi Amora`, remote_processing_allowed=true, test_mode=true falha com:

```text
POST /v1/web/utterance
conversation.service.observe_completed_utterance
cognition.resilient._prepare_context -> _record_input
cognition.store.put_journal
storage.remote.execute -> DatabaseError: SQLITE_CONSTRAINT
```

Isso confirma o defeito do adapter; a igualdade dessa causa com o incidente
real ainda exige comparar o traceback/código do staging. Latência de 5–6 s e
HTTP 200 sozinhos não provam lock, falha de STT ou qual constraint falhou.

## Causa reproduzida e correção de persistência

Conversation chama `cognition.observe_input` e a preparação do contexto registra
novamente INPUT_OBSERVED com a mesma chave de dedupe. Isso é idempotência
deliberada: `CognitiveStore.put_journal` deve recusar a segunda inserção UNIQUE
e retornar False. SQL envolvido: INSERT parametrizado em `cognitive_journal`,
constraint UNIQUE de `dedupe_key`. Nenhum valor de conversa/SQL real é logado.

O adapter anterior reconhecia somente códigos estendidos
SQLITE_CONSTRAINT_UNIQUE/PRIMARYKEY. Se o wire informa `SQLITE_CONSTRAINT` e
diagnóstico SQLite `UNIQUE constraint failed: ...`, ele lançava DatabaseError;
o store não podia reconhecer o dedupe e o turno terminava antes da speech queue.
O mock sempre enviava o código estendido do Python, escondendo essa diferença.

Agora somente a combinação código primário + prefixo fixo de diagnóstico UNIQUE
é normalizada para SQLITE_CONSTRAINT_UNIQUE/IntegrityError. O sufixo não é retido
nem logado. NOT NULL, FOREIGN KEY e constraints desconhecidas continuam erros,
não dedupe nem sucesso. Testes negativos verificam que seus writes não entram.
Nenhum SQL/schema/migration/domínio mudou. Não se adicionou retry: resultado
ambíguo continua fail-closed. O comportamento de baton corrigido anteriormente
permanece intacto e com todas as regressões passando.

Auditoria dos caminhos: language/social persiste events antes de conversation;
conversation persiste turno/decisão, cognition grava mensagens/journal/segmentos
e enfileira speech. Emotion/World/relationships mantêm seus gates canônicos;
test_mode não consolida aprendizagem. Stores usam transações curtas sob locks
locais; Event Bus chama handlers depois do append/commit. Claim é transacional;
ACK/cancel permanecem explícitos. O mock executa PRAGMA foreign_keys em cada
stream, BEGIN IMMEDIATE, statements reais e COMMIT/ROLLBACK, fechando streams
sem estado transacional. FTS, constraints, backups e restart seguem cobertos
pela suíte remota. Não se demonstrou nested transaction, lock persistente,
incompatibilidade RETURNING ou falha de FTS como causa deste incidente.

## Outras falhas corrigidas separadamente

1. **CLI pair:** chamava bootstrap para emitir um código. Agora usa apenas
   RuntimePaths/ensure, SystemClock e PairingManager no mesmo
   `paths.cache / pairing.json`. Mantém seis dígitos, TTL 300 s, cinco tentativas,
   uso único, arquivo 0600 e output. Teste chama o parser CLI `amora pair` com
   bootstrap e AmoraDatabase proibidos, e resgata o código no servidor já aberto.
   Não abre stores/Turso/providers/cognition. Não migra pairing para SQL.
2. **Observabilidade:** REQUEST_FAILED agora inclui method, template da rota
   (sem query/IDs arbitrários), correlation_id, exception_type, latency_ms, stage
   e storage_error_code de uma lista finita; quando identificado, inclui
   storage_module e operação do store. Etapas: stt.transcribe, language_social,
   conversation. Uma transcrição aceita que falha no journal é corretamente
   identificada como conversation, não como STT. Não há exception message,
   corpo, SQL, bearer ou keys no registro. O traceback ASGI técnico continua
   separado; console deixa de incluir o texto bruto da exceção nesse caminho.
3. **Safari:** `app-v2.js` é o script efetivamente servido. Claim não-401 deixa
   diagnóstico `fila/resposta indisponível: HTTP 500` e mensagem deduplicada;
   recuperação limpa o erro. Tratamento 401 continua intacto. Texto é adicionado
   antes do fetch TTS e permanece após falha/cancelamento. A UI não manda instalar
   voz no Termux quando o backend é remoto; mostra configurado/indisponível ou
   disponível conforme estado real do renderer. Timeout de fetch antes era
   descartado como AbortError de cancelamento e deixava a fila em preparing;
   agora é erro explícito, libera a fila e preserva texto. Cancelamento real
   continua suprimindo callbacks antigos. Nenhum fallback TTS foi criado.

## Evidência por fluxo

| Fluxo | Automação desta revisão | Staging da revisão |
|---|---|---|
| CLI pairing sem segundo Core, resgate/uso único | PASS | PENDENTE |
| Browser token, texto -> cognition -> resposta -> WEB_SAFARI | PASS | PENDENTE |
| WAV válido -> adapter Groq HTTP mockado -> transcript -> conversa/fila | PASS | PENDENTE |
| Claim, started, ACK/cancel | PASS | PENDENTE |
| Worker remoto responde 503: texto persiste, sem ACK oculto | PASS | PENDENTE |
| Backend worker realmente configurado no Northflank | Não observável aqui | PENDENTE |
| Áudio remoto Supertonic real/Safari | Não executado | PENDENTE |
| live/ready/restart e persistência remota | PASS sobre Hrana/SQLite real | PENDENTE |
| Log de erro identifica rota, etapa, store/código sem segredo | PASS | PENDENTE |

O provider cognitivo dos testes é o fallback local já existente quando não há
provider configurado. Não se substitui cognition por resposta fake. O STT usa
GroqWhisperSTT real com transporte HTTP mockado e WAV PCM válido; isso não é
certificação da conta/API Groq. O worker usa RemoteVoiceBackend real com erro HTTP
simulado; não se instalou Supertonic no Core nem se mudou identidade vocal.

## Testes e arquivos

- Pairing: `.venv/bin/python -m pytest -o addopts= tests/test_pairing.py -q`:
  **4 passed**, 1 aviso existente.
- Remoto: `.venv/bin/python -m pytest -o addopts= tests/test_remote_storage.py -q`:
  **26 passed**, 1 aviso existente.
- Gates específicos: `pytest -o addopts= -q` com
  `tests/test_cloud_web_incident.py`, `tests/test_remote_storage.py`,
  `tests/test_pairing.py`, `tests/test_web_safari_v101.py`,
  `tests/test_conversation_api_v08.py`, `tests/test_conversation_recovery_v08.py`,
  `tests/test_audio_api_v02.py`, `tests/test_cloud_runtime.py`,
  `tests/test_security_predeploy.py`, `tests/test_remote_voice.py`:
  **108 passed**, 1 aviso existente.
- Core completo: `.venv/bin/python -m pytest -o addopts= tests -q`:
  **366 passed**, 5 avisos existentes, sem skips.
- Node oficial 22.23.4: `node --test avatar/tests/*.test.mjs tests/web_browser.test.mjs`:
  **10 passed**; `node --check web/app-v2.js` PASS.

Implementação: `core/amora/__main__.py`, `storage/remote.py`, `api.py`,
`observability.py`, `web/routes.py`, `web/app-v2.js`. Regressões:
`tests/test_pairing.py`, `test_remote_storage.py`, novo
`test_cloud_web_incident.py` e `web_browser.test.mjs`.
`avatar-tests.yml` inclui os testes Web quando Web/harness mudar, mantendo
Node >=22.23.4 e as actions pinadas. Nenhum gate HIGH/CRITICAL é enfraquecido.

## Gate real necessário para encerrar

Após revisão/CI/merge por PR, publicar a revisão aprovada no staging através do
processo do operador (nenhuma configuração Northflank/secrets foi modificada).
No mesmo pod: `amora pair`, resgatar no Safari e enviar o payload mínimo acima.
Confirmar resposta não vazia em `/v1/conversation/next?device_id=WEB_SAFARI` e
texto no navegador. Capturar WAV autorizado, confirmar STT accepted, transcript,
turn e resposta. Conferir separadamente provider/backend configurados e áudio
Supertonic remoto; erro de worker deve deixar texto visível e erro explícito.
Conferir live/ready, restart, persistência e os logs sanitizados novos.

Se o staging continuar em 500, usar method/path/stage/storage_module/operation e
storage_error_code para identificar a constraint real. Não assumir que UNIQUE
é a causa se o código/trace indicar NOTNULL, FOREIGNKEY ou outra operação.
Não adicionar retry ou aceitar DatabaseError genericamente para passar o gate.
