# Auditoria de migração Core/Web, persistência e voz

Data: 2026-10-23. Base auditada: `3a275b9` (main selecionado no onboarding).
Decisão de BK: preparar Core/Web para Koyeb, estado para Turso/libSQL e voz
para Modal, todos substituíveis. Não houve execução nesses serviços nesta auditoria.

## Inventário e acoplamentos

| Área | Evidência no código | Consequência / fronteira necessária |
|---|---|---|
| SQL | `storage/database.py` e stores de actions, autonomy, avatar, cognition, conversation, emotion, memory, world abrem nove conexões sqlite3 | Injetar backend por instância; nenhum seletor global que contamine testes |
| SQL local | Os nove constructors repetem foreign_keys/WAL/synchronous e Row | WAL é detalhe SQLite local; não enviar pragmas locais ao remoto |
| Migrations | database.migrate executa dez scripts com BEGIN IMMEDIATE + ledger na mesma transação | Preservar atomicidade do DDL e ledger, sem split ingênuo por ponto e vírgula |
| Constraints | UNIQUE dedupe, FK CASCADE/SET NULL, CHECK bounded, ON CONFLICT e AUTOINCREMENT nas migrations | Testar constraints e rollback no adapter; não converter erros de rede em dedupe |
| Retrieval | memory/store.py cria FTS5 fora do ledger, MATCH/bm25, unicode61; fallback lexical + entity/relationship + RRF/MMR | Capability FTS explícita; indisponibilidade não pode se disfarçar de ausência de FTS |
| Backup | database.backup_to/restore_backup usam sqlite3.backup e integrity_check | Snapshot remoto precisa transação consistente/export próprio, nunca copiar cache local como verdade remota |
| Assets | avatar/store.py grava bytes fora do SQL; backup .avatar.zip verifica tamanho/SHA contra metadados do snapshot | Banco remoto sozinho não permite filesystem efêmero; blobs precisam storage durável e restore validado |
| Paths | RuntimePaths e ~/.amora; pairing.json, secrets, logs/cache e modelos/lib nativas | Separar estado durável de cache; token estável injetado, secrets nunca na imagem |
| SecretStore | env AMORA_SECRET_* sobrepõe vault em arquivos | Interface já atende injeção; cloud deve exigir token estável sem gerar identidade de acesso nova a cada restart |
| Scheduler | heap em memória; autonomy.schedule_idle é opt-in; servidor não chama run_due | Não ativar autonomia nova silenciosamente. Jobs não são distribuídos/duráveis; primeira implantação uma réplica |
| Event Bus | EventStore Protocol; append antes dos handlers; dedupe memória + SQL | At-most-once dispatch após persistência; queda entre append/handler perde efeito. Outbox/recovery é etapa separada, não alegar exactly-once |
| Streaming | /v1/events/stream autentica Bearer; fila bounded 128 descarta mais antigo; espera queue.get | Não é replay durável. Disconnect sem novos eventos pode manter subscription; SECRET precisa mesmo gate de AUTH |
| Web | web/routes.py e web/app-v2.js preservam Core ownership, test_mode e WEB_SAFARI | Manter contrato, shell público e APIs privadas autenticadas; TLS pelo ingress |
| Voz | web/routes instancia AmoraLocalVoiceRenderer; asyncio.to_thread; render inteiro antes do WAV | VoiceBackend injetável; remoto recebe apenas texto e expressão, não SpeechUtterance inteiro |
| Nativos | voice/local_amora.py lazy importa sherpa_onnx e ctypes WORLD; DSP C++ compartilhado com JNI | Core funciona sem modelos/nativos; worker usa mesmo renderer/DSP, sem aproximação Python |
| Delivery | conversation/store claim/ACK/cancel e cognition speech_segments | Render não é ACK. Leases e leitura→update requerem transação real; RLock só protege uma instância |
| Lifecycle | CLI uvicorn bind fixo localhost; core.close sequencial; console hook legado condicional | ASGI lifespan + shutdown idempotente; configuração host/port; readiness não chama health social caro |
| Observabilidade | JSON rotating file, terminal dashboard; journal/usage/action results persistentes | stdout JSON opt-in cloud; snapshot autenticado com allowlist; erros agrupados por código/fingerprint, logs separados |
| CLI/testes | CLI reutiliza bootstrap; testes isolam AMORA_HOME; testes de localhost e A15 validam adapter | Preservar modo local offline; não substituir fixture por conta cloud |

## Interfaces que já servem

EventStore, Clock/SimulatedClock, Scheduler, ModelRouter/provider descriptors,
PrivacyScope, DialoguePlan/RealizationFrame, PluginRegistry/ActionBus e
SpeechUtterance são fronteiras úteis. Os stores já concentram SQL por domínio;
não reescrever identidade, memória, relações, emoções ou planner.

## Fragilidades encontradas

1. `health` executa health_report completo (inclui leituras sociais e decay) e
   devolve HTTP 200 mesmo com status degraded; não existe readiness.
2. `close` para no primeiro erro; hook antigo de shutdown pode não existir.
3. claim usa SELECT antes do primeiro write; sqlite context manager não inicia
   transação no SELECT. Dois stores/processos podem reivindicar a mesma fala.
4. ACK/cancel leem fora da transação e podem sobrescrever cancelamento concorrente.
5. Event Store e vários stores capturam todo IntegrityError como duplicata;
   CHECK/FK não são duplicatas. Não mascarar como sucesso no novo adapter.
6. `_ensure_fts` captura OperationalError indiscriminadamente; timeout remoto não
   prova FTS indisponível. Índice recriado não faz backfill do histórico automaticamente.
7. assets têm rename de arquivo antes do commit SQL; snapshot concorrente pode
   falhar na verificação. Preservar falha explícita, nunca publicar backup parcial.
8. WebSocket filtra AUTH mas não SECRET; fila não tem replay e perde eventos sob pressão.
9. worker local não interrompe cálculo nativo quando o fetch é abortado. Cancelar
   delivery continua obrigatório; não prometer cancelamento de compute remoto.
10. pairing em arquivo é local à réplica e não serializa consumo entre processos.

Itens não corrigidos em uma etapa permanecem limitações, não bugs resolvidos.

## Antes do deploy

- Gate de interfaces locais e suíte existente antes de ativar qualquer backend remoto.
- Container Linux com assets Web/Avatar incluídos, usuário não-root, bind/port,
  readiness real, shutdown, stdout JSON e secrets injetados.
- Uma réplica/um processo enquanto social state, scheduler e pairing forem locais.
- Storage remoto com transações stateful; HTTP SQL stateless não substitui DBAPI.
  Não retry de writes/commit ambíguos; sem fallback silencioso para SQLite vazio.
- Backup/export e restore incluindo assets, FTS e ledger; validar recuperação antes
  de mover dados reais. Não provisionar conta ou alterar banco de produção implicitamente.
- Worker autenticado por credencial de serviço distinta do token do usuário;
  payload mínimo, TLS, timeout, bounds, WAV validado, nenhuma outra voz fallback.
- Medir latência de SQL remoto síncrono em endpoints async e concorrência real.
- Configurar custo/quotas/retention de Koyeb/Turso/Modal com BK; sem ativar billing.

## Mudanças proibidas nesta migração

Não redefinir personalidade, self-facts, relações, DialogueAct, política de custo,
consentimento ou parâmetros vocais. Não remover SQLite, FTS, testes, migrations,
backup, ACK ou cancelamento. Não mover lógica canônica ao Safari/worker/provider.
Não retunar Supertonic INT8 60/40, FINAL_B_PLUS60, Signature V1 Soft ou bounds.
Não substituir por Gemini/Safari TTS. Não afirmar certificação física por mocks.

## Sequência de revisão

1. Auditoria + injection de persistência e voz, sem mudança de domínio.
2. Core/Web Linux e lifecycle/configuração seguros.
3. Adapter SQL remoto + capabilities + recuperação/persistência de assets.
4. Transporte remoto de voz e implantação worker independente.
5. Snapshot operacional seguro e dedupe de erros para Living Console/Dev MCP.

Cada etapa deve passar os testes anteriores antes da próxima. Credenciais,
região, escala, orçamento, retenção e certificação acústica continuam decisões de BK.
