# RUN 01 — Durable Cognitive Runtime: handoff inicial

**Status:** PLANNED; não é implementação.
**Pré-requisito:** RUN 00 persistida em main.
**Leia primeiro:** [PLAN.md](PLAN.md), seções 0–4 e 11–13, [RUN_LOG.md](RUN_LOG.md),
AGENTS.md e canon. Issue [#376](https://github.com/Sllay/Amora-Chibi/issues/376).

## Missão e fronteira

A primeira entrega é tornar a resposta um trabalho durável do Core,
independente do tempo de vida da requisição HTTP. A mensagem aceita precisa
sobreviver a queda do provider e do processo; uma falha de geração não
equivale a uma resposta final. Não implementar agora todo AMD, web, visão
avançada, streaming segmentado nem trocar modelos. RUN 02 completa a
política sofisticada de recuperação; RUN 01 precisa entregar fila e retomada
funcionais com uma classificação mínima conservadora de falhas.

## Inspeção antes de escrever código

Confirmar HEAD e estado de produção, backend/versão de schema, migrations,
flags, contratos de serviço e suites relevantes. Ponto de partida observado:
- core/amora/messaging/service.py: receive, _receive_owned, _receive_once,
  observe_input_async, reflexão, timeout e preparação de outbound.
- core/amora/messaging/generation_lifecycle.py, storage_lane.py,
  store.py, postgres_store.py; core/amora/storage/postgres_runtime_generation.py
  e inventário SQL/migrations.
- core/amora/messaging/reflection_queue.py: reconciliação e leasing de trabalho
  existente, sem duplicar consolidação.
- core/amora/cognition/structured_runtime.py: fallback_plan, erros,
  persistência de realização e invalidação.
- workers/common/core-client.mjs, workers/discord/worker.mjs: contrato
  inbound/outbound, typing/progress, retry, nonce, entrega/ACK.
- lifecycle do Core/ASGI, scheduler e rotas messaging; localizar arquivos
  reais a partir do HEAD, não assumir nomes.
- testes existentes: test_messaging_cognition_budget.py,
  test_discord_generation_incident.py, test_postgres_run2_messaging_queue.py,
  test_numes_heartbeat_adaptive.py; mapear suites adicionais.

## Sequência de trabalho (subetapas internas da RUN 01)

A. ADR e contratos: ownership; estados/transições; compatibilidade do ACK;
   eventos de origem; privacidade; política de fila, backpressure e ordenação;
   recuperação de lease, cancelamento e entrega. Determinar campos/índices e
   migrations com código real. Não gravar envelope inteiro sem revisar
   campos sensíveis. Atualizar diário com baseline e riscos.
B. Persistência: enqueue idempotente, claim, fencing/renewal, checkpoint,
   retry_wait, bloqueio, reconciliação e invalidation para SQLite e PG18.
   Reusar armazenamento e dedupe existente; não manter transação durante LLM.
C. Runtime: dispatcher leve, limite global e por sessão, ciclo startup/shutdown,
   geração fora do inbound HTTP, retomada após crash, status de pendência.
   Integrar com gates existentes e memória observada uma única vez. Fallback
   técnico temporário não é mensagem social final.
D. Transportes: ACK ingress distinto de resposta concluída; worker Discord
   consome outbox/ACK existente. Dedupe no reenvio, retry de rede, edits/deletes,
   shutdown e compatibilidade. Não alterar voz/Web em escopo não certificado.
E. Ensaios: matriz abaixo, CI, revisão de migração/rollback, deploy canário
   autorizado e live no Northflank com único Gateway. Documentar todos os
   resultados, atualizar estado de RUN e comentar issue #376.

## Matriz mínima de aceitação

| ID | Cenário | Resultado esperado |
|---|---|---|
| D01 | Mensagem válida; provedor normal | ACK após persistência, resposta final entregue uma vez |
| D02 | Corte HTTP após aceitação | Job permanece; worker retry não cria outro turno |
| D03 | Morte entre registro e enqueue | Reconciliação recupera sem perder original |
| D04 | Morte durante geração | Lease recuperável, job retoma e entrega |
| D05 | Morte após checkpoint de resposta | Reusar resposta preparada, sem nova realização desnecessária |
| D06 | Morte entre outbox e ACK | Entrega com nonce/dedupe e reconciliação |
| D07 | Provider temporariamente indisponível | Estado retry_wait, próxima tentativa persistida, sem erro social terminal |
| D08 | Provider permanentemente bloqueado | Causa específica, mensagem preservada sob política e possibilidade de retomar |
| D09 | Nova mensagem na mesma sessão durante retry | Ingresso aceito, ordem causal preservada, contexto anterior disponível |
| D10 | Sessões distintas e fila cheia | Limites, fairness/backpressure, nenhuma perda silenciosa |
| D11 | Lease expira enquanto execução antiga tenta concluir | Fencing rejeita escrita antiga |
| D12 | Edit/delete/cancel/revogação | Invalidar trabalho e entrega obsoleta; não ressuscitar conteúdo |
| D13 | Reflexão/memória durante retry | Dedupe e provenance; input observável sem resposta final |
| D14 | Conteúdo AUTH/SECRET ou não autorizado | Nenhum conteúdo proibido na fila, logs ou provider |
| D15 | Mensagem com anexo e processo reinicia | Não persistir bytes/URL assinada; texto preservado, estado visual honesto |
| D16 | Rollback com jobs pendentes | Sem apagar nem duplicar trabalho; procedimento verificável |

Todos os cenários aplicáveis em SQLite e PostgreSQL real sintético;
testes de integração Core/worker. Testes de timing com relógio controlado
e deadlines pequenos, sem aguardar minutos reais em CI. Live é uma camada
separada; registrar SHA efetivamente implantado, medições e evidência.
Se live estiver indisponível, não marcar COMPLETED.

## Fora de escopo

Altare/deploy concorrente, migração de hospedagem, troca de banco, mudança
de personalidade/voz/ACIR, instalação de frameworks externos, refatoração
global não necessária. RUN 02 calibra SSE, retry/failover e 413 com profundidade;
RUN 03 otimiza/decompõe contexto; RUN 04 recupera anexos; RUN 05 melhora web;
RUN 06 integra continuidade e segmentos. Não implementar stubs como se
fossem essas entregas.

**Antes de concluir:** preencher registro RUN 01 em RUN_LOG.md, atualizar
PROJECT_STATE.md e canon/ADR quando necessário, registrar PR/SHA/CI,
resultados, falhas e correções, e deixar instrução explícita para RUN 02
somente depois do gate ou de exceção aprovada.
