# Incidente de lifecycle Hrana e usage ledger

Data: 2026-10-25. Base main `ae3b2f610056c5e8b7629a9f538f8f8346a590f3`.
Branch: `fix/libsql-connection-recovery`.
Decisão: [ADR-0020](../../decisions/ADR-0020-libsql-session-recovery-and-usage-boundary.md).

## Root cause final

Evidência real fornecida por BK, POST /v1/audio/transcribe, stage conversation:

| Hora UTC | Store/operação | Resultado |
|---|---|---|
| 18:04:25 | amora.cognition.store / record_usage | SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN, 24.5 s |
| 18:04:33 | amora.cognition.store / get_message_by_dedupe | SQL_CONNECTION_CLOSED, 1.9 s |
| 18:04:42 | mesma leitura | SQL_CONNECTION_CLOSED, 2.0 s |
| 18:04:46 | mesma leitura | SQL_CONNECTION_CLOSED, 1.8 s |

Primeira correlação: 600bf18d-3302-4cca-bf56-18f4f0ac687c. As seguintes foram
0ed6172b-f383-4d86-9295-ac3bc3980abe, dffa2cba-3a72-4522-adc4-a2915d535fbf e
f039aa3f-e024-46c2-ac95-5847251405f5. Os frames httpx/httpcore comprovam a fronteira
de transporte; sem resposta confiável não se sabe se INSERT/COMMIT foi aplicado.
Os logs não identificam o motivo físico específico da falha de rede, e o patch
não assume indisponibilidade permanente ou erro de URL/auth/STT.

_pipeline marcava broken e descartava baton, corretamente impedindo continuar a
transação. Mas seu guard tratava broken como close terminal. CognitiveStore._conn
era o mesmo objeto nos requests seguintes. A recuperação exigia restart, embora
o Core e outras conexões/probes continuassem saudáveis. Reproduzido antes do fix:
dois testes falharam exatamente na leitura após record_usage (INSERT/COMMIT).

## Fix e provas de ausência de retry

_ensure_ready, no adapter, troca somente o client/pool/sessão de uma conexão idle
poisoned. O erro anterior já retornou e seu contexto já terminou. Nenhum SQL ou
argumento é retido/reexecutado. A conexão lógica continua sendo o mesmo objeto.
Fresh stream começa com baton=null e PRAGMA foreign_keys=ON; tokens/opções TLS,
origins e seleção do backend não mudam. Explicit close permanece terminal.

Contagem observada no mock Hrana com SQLite real, por operação original:

| Injeção | Pipelines HTTP na operação que falha | SQL write enviado | COMMIT enviado | Próxima leitura |
|---|---:|---:|---:|---|
| INSERT/UPDATE/DELETE autocommit, perda antes/depois da execução | 1 | 1 | 0 explícitos | 1 pipeline novo com bootstrap + SELECT + close |
| record_usage, resposta de INSERT perdida | 2: BEGIN + INSERT | 1 | 0 | Fresh client/stream, leitura normal |
| record_usage, resposta de COMMIT perdida | 3: BEGIN + INSERT + COMMIT | 1 | 1 | Fresh client/stream, leitura normal |

O primeiro caso confirma ambos os resultados físicos possíveis, sem inferi-los
no client. Na simulação, perder INSERT transacional abandona/expira stream;
perder COMMIT pode deixar row durável. Nenhum caso reenviou o write. O ledger
observado pode ter 0/1 registro desse attempt, nunca uma tentativa de compensação.
O mock também rotaciona batons como o protocolo, fecha terminadores válidos e
reproduz códigos primários de constraint/SQL real; não é stub de HTTP 200.

Dentro de transação broken, tentativa de SELECT acrescenta zero HTTP requests
e mantém o mesmo client. Depois do unwind, a operação independente usa outro
client, o anterior está fechado e os batons anteriores nunca reaparecem.
FK é testada com violação real após recovery. COMMIT/ROLLBACK válidos com baton
nulo, stream perdido, constraints e rowcount/dedupe continuam cobertos.

## Auditoria de todas as conexões persistentes

| Owner | Referência e prova depois de poison |
|---|---|
| CognitiveStore / store_v1 | Mesmo _conn; get_message_by_dedupe, put_journal e list_messages |
| MemoryStore | Mesmo _conn; count, remember e retrieval FTS |
| ConversationStore | Mesmo _conn; pending_count e transação independente |
| EmotionStore | Mesmo _conn; event_count e transação independente |
| WorldStore | Mesmo _conn; claim_count e transação independente; SelfModel/relationships usam esse owner |
| AutonomyStore | Mesmo _conn; decision_count e transação independente |
| ActionStore | Mesmo _conn; plan_count e transação independente |
| AvatarStore | Mesmo _conn; project_count, transação e leitura de blob prévio |
| AmoraDatabase | Mesmo _conn; event_count e transação independente; Event Store/registry/sessions compartilham owner |

SQLBlobStore guarda referência à conexão de AvatarStore. Como não se substitui
o wrapper lógico, seu acesso ao blob remoto também recupera corretamente.
Backup/restore abrem/usam a mesma interface de conexão e continuam fail-closed;
nenhuma operação parcialmente falha é reiniciada automaticamente.

## Usage ledger: falha explícita sem apagar realização válida

UsageRecorder é a única fronteira best-effort. CognitiveStore.record_usage direto
continua levantando o erro original; journal/memória/fila não entram no catch.
Uso confirmado retorna persisted somente após COMMIT. Resultado incerto gera
USAGE_LEDGER_PERSISTENCE_UNCERTAIN, sem retry e sem inserir linha falsa de falha.
Constraint conhecida gera USAGE_LEDGER_PERSISTENCE_FAILED. Exceções de
programação fora de DB-API continuam falhas. A realização válida prossegue com
metadata de persistence e próximas operações independentes recuperáveis.

Estado DEGRADED fica visível em cognition e /v1/operations/state administrativo,
com fingerprint/contagem, turn ID e horários. Uma nova escrita confirmada não
resolve retroativamente a incerteza. Contadores são bounded/local ao processo;
o ledger contém exclusivamente rows realmente lidas do banco. Quota observada
do provider continua valendo, inclusive remaining_requests=0/cooldown. Não se
usam totais incompletos como prova de quota disponível.

Logs distintos: storage_connection_poisoned, usage_ledger_persistence_uncertain,
storage_connection_recovered. Apenas códigos/classes/IDs/generation, sem SQL,
URL, bearer, tokens, conversa ou hidden reasoning. Campos de erro HTTP para
falhas obrigatórias continuam com method/path/stage/correlation_id sanitizados.

## Achados adicionais corrigidos

- BEGIN ativava transaction antes do guard de recovery e fechava terminalmente
  em qualquer erro; agora prepara recovery antes do BEGIN e abandona seu contexto.
- executescript com BEGIN próprio podia deixar transaction ativa após falha.
- Contexto que engolia erro de conexão podia terminar sem falhar; agora não
  simula COMMIT bem-sucedido.
- Reflection podia ler um 429 de outro turno se o usage atual não persistisse;
  só o status do próprio turno é elegível. Não altera Provider Mesh ou sua ordem.

## Validação e gate real

Resultados locais finais, sem skips:

- Core: **452 PASS**, cinco avisos preexistentes. Mesmo resultado no container
  Alpine/musl non-root/read-only, sem rede, com cap-drop ALL e no-new-privileges.
- Seleção explícita de regressões: **196 PASS**, um aviso preexistente. Inclui
  **27 casos novos de recovery** e sete da fronteira usage/STT, além de remote
  storage/journal, cognition, APIs Web/STT, Memory/World, pairing, cloud/security.
- Node **22.23.4**, Avatar/Web existentes: **50 PASS**, sem alteração de JS/UI.
- Container build/smoke: **PASS**; pip check: **No broken requirements found**.
- Ruff nos módulos/fixtures/testes novos e adapter; git diff --check: **PASS**.

Imagem local: `sha256:bf72f246194fa64f40f3bbd36a95bfd26374c432fe7f3915e4285062c4e161d5`.
Base, locks, Dockerfile, scanner/gate HIGH/CRITICAL e infraestrutura inalterados.
CI e SHA finais ficam no handoff/PR; test_mode permanece inalterado.

Comandos executados na raiz do checkout:

```bash
.venv/bin/python -m pytest -o addopts= tests -q
.venv/bin/python -m pytest -o addopts= tests/test_remote_connection_recovery.py -q
.venv/bin/python -m pytest -o addopts= \
  tests/test_remote_storage.py tests/test_remote_connection_recovery.py \
  tests/test_journal_idempotence.py tests/test_usage_recovery_stt.py \
  tests/test_cognition_v10.py tests/test_v10_dialogue_emotion_integration.py \
  tests/test_cognitive_failover_v10.py tests/test_cognition_backup_v10.py \
  tests/test_cloud_web_incident.py tests/test_web_surface_target.py \
  tests/test_web_safari_v101.py tests/test_web_safari_device_routing_v101.py \
  tests/test_memory_v03.py tests/test_world_v04.py tests/test_cloud_runtime.py \
  tests/test_security_predeploy.py tests/test_pairing.py tests/test_pairing_api.py -q
node --test avatar/tests/*.test.mjs tests/web_*.test.mjs
python3 tools/cloud/smoke-container.py amora-core:libsql-recovery
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,size=256m \
  amora-validation:libsql-recovery
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges amora-validation:libsql-recovery \
  /testenv/bin/python -m pip --python /opt/venv/bin/python check
```

Scenario E2E automatizado: bootstrap saudável → diálogo Web válido → WAV Groq
realista → realização válida → usage perde resposta → texto na fila Safari →
outro WAV/STT/realização sem restart. Uma realização por turno, um INSERT de usage
por realização; ledger tem somente rows confirmadas por leitura, estado de gap
continua incerto. /live e /ready permanecem saudáveis e não há request_failed.
Falha injetada em journal obrigatório ainda produz HTTP 500, seguida de recovery
no turno independente seguinte; não há conversão global de DatabaseError em sucesso.

Nenhum deploy, rotação de secret/token, mudança de réplica/config Northflank,
schema, UI, voz, PCM, canon emocional, personalidade ou política de memória.
Sem credenciais de staging neste ambiente: a revisão precisa ser validada em
Northflank/Turso reais após a promoção autorizada. Logs locais/CI não certificam
expiração de streams/locks ou rede física; nenhuma validação real foi inventada.
