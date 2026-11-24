# Amora Cognitive Evolution V2 — diário permanente de RUNs

**Plano:** [PLAN.md](PLAN.md) | **Issue de origem:** [#376](https://github.com/Sllay/Amora-Chibi/issues/376)

Este diário é o handoff operacional entre pessoas, chats e agentes.
Atualizar **no mesmo PR de cada RUN**, incluindo resultados negativos e
correções. Não apagar registros antigos: adendar com data e referência.
Dados privados, tokens, prompts brutos, URLs assinadas e dumps de banco
não são permitidos neste arquivo.

## Estados e critérios de avanço

| Estado | Significado |
|---|---|
| PLANNED | Escopo aceito, sem implementação certificada |
| IN_PROGRESS | Implementação ativa, gate aberto |
| IMPLEMENTED | Código completo revisado, ainda sem toda validação |
| CI_PASS | Testes e contratos relevantes verificados no commit exato |
| LIVE_PENDING | Teste real necessário não realizado |
| LIVE_CERTIFIED | Cenários reais exigidos comprovados no ambiente |
| BLOCKED | Impedimento identificado, evidência e próximo passo registrados |
| COMPLETED | Gate completo da RUN satisfeito, incluindo live quando exigido |
| ROLLED_BACK | Reversão executada, causa e efeitos registrados |

CI_PASS e LIVE_PENDING podem coexistir. Não usar COMPLETED como sinônimo de
"PR criado", "arquivo escrito", "teste mock aprovado" ou "merge".
A RUN 00 é exclusivamente documental: seu gate é persistência e revisão das
instruções, não certificação de código ou ambiente. Cada RUN seguinte tem
gate próprio em PLAN.md. Status de produção deve ser verificado separadamente.

## Quadro de progresso

| RUN | Área | Estado inicial em RUN 00 |
|---|---|---|
| 00 | Preparação e continuidade | Gate documental aprovado; PR #377 (conclusão condicionada ao merge) |
| 01 | Durable Cognitive Runtime | CI_PASS — 9/9 no 6163c34; LIVE_PENDING até bateria final |
| 02 | Intelligent Provider Orchestrator | IN_PROGRESS — branch empilhada sobre RUN 01 |
| 03 | Adaptive Context & Comprehension Engine | PLANNED |
| 04 | Advanced Multimodal Perception | PLANNED |
| 05 | Autonomous Web Intelligence | PLANNED |
| 06 | Living Conversation Engine | PLANNED |
| 07 | Cognitive Integration & Certification | PLANNED |

## RUN 00 — 2026-11-23 — preparação

**Objetivo:** preservar integralmente as decisões da conversa sobre falhas
de provedor, mensagens extensas, imagem+texto, web e experiência social.
Preparar handoff que não dependa do agente original.

**Evidência de origem:** issue #376; log de 23/11/2026 com stream Numes HTTP 200
cancelado pelo caller em 87 s, Groq 413, fallback sem tempo, e reflexão
independente já concluída. PLAN.md mantém os números e caminhos de código
observados. Não afirmar que esta é a causa de todas as falhas de produção.

**Decisões aceitas:** sete RUNs, um orquestrador cognitivo com módulos
especializados, estado persistente do Core, priorização de resiliência antes
de decomposição, visão/web/diálogo e certificação final. Preservar identidade,
ACIR, memória, voz e permissões; não instalar frameworks inteiros ou Chromium
permanente; manter Northflank e não iniciar outro Discord no Altare.
A fila deve permitir retorno de respostas após falhas recuperáveis, sem
reenviar a mensagem e sem retries ilimitados agressivos.

**Trabalho realizado:** criação do plano e deste diário; ligação às
instruções de agentes e checkpoint do projeto nesta revisão. O plano separa
baseline observado, metas, arquitetura proposta e decisões ainda a fechar.
Não muda schema, código, configuração, deploy, memória real ou secrets.

**Erros/correções:** incidente operacional original documentado no plano;
nenhum hotfix aplicado nesta RUN. Nenhuma certificação runtime alegada.
O problema de instalação no Altare é outro assunto, fora do escopo.

**Validação RUN 00:** conferir leitura dos documentos no GitHub, links relativos,
cobertura I01–I10/RUN 01–07, gates, regras obrigatórias e instruções de continuidade.
Releitura das seis alterações no GitHub e verificação estrutural: PASS para
7 seções RUN, 10 ideias, regras obrigatórias, 16 cenários RUN 01, modelo de
diário e referências cruzadas. [PR #377](https://github.com/Sllay/Amora-Chibi/pull/377),
base de main 41754f8709ec6760bf85c935641d99e4cf205b8f. Nenhum workflow de CI
associado foi retornado na consulta inicial; não declarar CI/runtime PASS.
A conclusão da RUN 00 é efetiva após merge e releitura em main; registrar
SHA final no comentário de #376. O histórico Git prova a persistência.

**Próximo passo:** iniciar RUN 01 com [RUN01_HANDOFF.md](RUN01_HANDOFF.md).
Não declarar nenhuma das sete RUNs pronta antes da implementação e testes.

## RUN 01 — 2026-11-23 — início / subetapas A–B

- Status: IN_PROGRESS; gate aberto. Base documental main 079c42e83760b5e9f15032c7496d5bc19780acda (PR #377); branch feat/cognitive-evolution-run01-durable-runtime; issue #376.
- Inventário: schema.py mantém 18 default e 19 opt-in certificado. migrations_v108.py já contém reflection jobs e generation lease/staging. O lease existente não representa uma fila de respostas. storage_lane.py usa ownership dedicado para SQL remoto. PostgreSQL exige registry de SQL revisado. Worker inbound ainda aguarda receive síncrono.
- Decisão inicial: não modificar schema ativo nem aplicar DDL no Northflank. Começar contrato candidato isolado, testável, que referencia input já persistido e não duplica texto, anexos, tokens ou URLs. Reusar padrões de episode_work/InboundGeneration; integrar ownership/fencing/outbox em etapas seguintes desta mesma RUN, com migration explícita e prova PG.
- Baseline live não medido nesta etapa; confirmar backend efetivo e revisão implantada antes de rollout. Não trocar host nem desativar gateway.
- Gate: aberto; subetapas C–E e integração real pendentes. Nenhuma execução de produção autorizada por este registro.

### RUN 01 — incremento inicial A–B (SQLite candidato)

- Implementação: [ADR e evidências](RUN01_DURABLE_REPLY_ADR.md); DDL em core/amora/storage/reply_job_schema.py e repository em core/amora/messaging/reply_jobs.py. Commits c1fe272, 7b6f3bd, b7928c4, 52bd482 e e4c92c6. Não registrado nas migrations; não ligado ao Core.
- Correções na revisão: FK CASCADE para permitir exclusão autorizada do input; claim impede concorrência dentro da sessão mesmo se job bloqueado antigo voltar à fila. Precisamos provar o fence comum com o outbox antes de ativar.
- Testes adicionados: tests/test_reply_jobs_candidate.py, sete cenários unitários/integração de armazenamento SQLite, relógio simulado, reconciliação, dedupe, lease expirada, duas conexões, retry, invalidação, bloqueio e ACK. Execução CI ainda não comprovada neste registro.
- Pendências desta mesma RUN: SQL PG revisado, prova no lab real, migration/restore/rollback, produtor de entrada com registro original e envelope recuperável, dispatcher/ownership compartilhado, status/outbox, transporte, matriz D01–D16, benchmark e certificação live. Não começar RUN 02. Nenhuma alteração em produção.

### RUN 01 — CI inicial e correção (23/11/2026)

- PR #378, HEAD inicial b310a4a4. Workflows de memória/percepção, modalidade, segurança e containers: PASS. Core tests #45283534381: Python 3.11 com 2.117 passed, 128 skipped e 1 failed; outras versões canceladas pela matriz.
- Falha: test_candidate_dedupes_and_recovers_expired_lease_across_connections, linha 108. As etapas anteriores (dedupe, lease expirada, retomada, fencing, atraso) passaram. A asserção final usava ConversationMessage.text, atributo inexistente.
- Causa confirmada em cognition/models.py: campo de entrada original é raw_text. Commit 05f019e corrige a asserção para comparar raw_text == request.text sem enfraquecer a verificação nem alterar runtime.
- Nova certificação CI necessária; gate RUN 01 aberto. PostgreSQL, integração Core/Discord, migration e live seguem pendentes.

### RUN 01 — incremento PostgreSQL e ownership compartilhado (23/11/2026)

- Baseline de validação do recorte anterior: HEAD 2084e3c, 5/5 workflows PASS,
  matriz Python 3.11–3.14 PASS; 2.118 passed / 128 skipped no 3.11.
  [Core tests 45284563242](https://github.com/Sllay/Amora-Chibi/actions/runs/45284563242).
- Implementação seguinte: SQL fixo SQLite + 14 operações PG revisadas
  (13 operações e lock de claim), DDL PG18 sintético explícito, CI lab
  reaproveitado. Repository único; registry padrão não importa os SQLs
  candidatos. Claims/renovação/release usam a mesma autoridade/token de
  InboundGeneration na transação, preservando prepared em retry.
- Testes: suíte parametrizada com 11 cenários por backend, mais contrato
  de statements. Inclui disputa de jobs de sessões distintas, rejeição de
  metadados, lease preexistente, checkpoint recuperado e stale owner no outbox.
  Novos testes adicionados; não declarar PASS até execução no HEAD atualizado.
- Commits e detalhes em [ADR candidato](RUN01_DURABLE_REPLY_ADR.md).
- Próximos gates: CI real PG18, migration/rollback, produtor e reidratação
  de input autorizado, dispatcher, finalização/reconciliação atômica,
  invalidação e cancelamento concorrentes, ACK do worker, matriz D01–D16
  e live. Nenhum deploy, alteração de schema ativo ou troca de banco.
  RUN 01 permanece IN_PROGRESS.

### RUN 01 — validação do incremento PG18 e ownership (23/11/2026)

- Revisão de código certificada em laboratório: `c65955c4616a16b6c88ba66b1b0bf37d3f786dcc`, PR #378 ainda draft.
- Seis workflows concluídos com sucesso. [Laboratório PG18](https://github.com/Sllay/Amora-Chibi/actions/runs/45288939524): 220 testes aprovados, zero falhas, 3 avisos, 87,19 s. Logs confirmam os 23 casos de test_reply_jobs_candidate.py: 11 SQLite, 11 PostgreSQL 18 e 1 contrato SQL, todos PASS.
- [Core tests](https://github.com/Sllay/Amora-Chibi/actions/runs/45288939669): Python 3.11–3.14 PASS; Python 3.11 com 2.123 aprovados, 139 ignorados, zero falhas, 8 avisos, 144,34 s. Os demais workflows (segurança, containers, memória/percepção e modalidade) PASS.
- Nenhuma falha nova a corrigir; nenhuma alteração de código necessária. Este registro documental não altera a revisão de código validada.
- Evidência limitada à persistência candidata, concorrência, leases e checkpoints em laboratório. Gate geral da RUN 01 continua aberto: migração revisada, reidratação autorizada de input, dispatcher, finalização/invalidação e reconciliação atômicas, ACK Discord, matriz D01–D16 e certificação live. Northflank, Altare e bancos ativos intactos. Próximo passo: integração real com Core, sem ativação prematura.

### RUN 01 — integração candidata Core/Discord (23/11/2026)

- PR #378 permanece draft. A fila foi ligada ao serviço de mensagens via configuração interna explícita e ao ciclo de vida ASGI. Não existe ativação automática, migration de produção ou alteração do registry PG padrão.
- Escopo inicial conservador: texto em DM Discord do proprietário verificado, PERSONAL e consentimento remoto; comandos locais, grupos, web, anexos, visão e expressões recebidas continuam na rota legada. O catálogo de expressões não é persistido no envelope; o caminho candidato produz texto. RUN 04/05/06 devem estender contratos de recuperação multimodal/web/modalidades sem regressões.
- Implementação: durable_reply_runtime.py (admissão, hidratação via audit/input, ACK sem geração, dispatcher, lease renovável, retry_wait, checkpoint e reautorização); messaging/service.py e storage_lane.py (entrada, wake após release, operações no lane existente); cognition/durable_realization.py, service.py e structured_runtime.py (fallback operacional não vira fala; realização estruturada validada antes do commit; self-choice gate original com revalidação); reply_jobs.py e SQLs candidatos (commit único de histórico/segmentos/journal/outbox/audit/ready, dedupe e conflito explícito); messaging/store.py (revalidação do dono e fonte no claim de outbound). Worker existente já recebe ACK sem outbound imediato e faz polling/ACK do outbox; teste real do Gateway ainda não executado.
- CI do recorte inicial `1e65c2a`: [PG18 lab 45303702601](https://github.com/Sllay/Amora-Chibi/actions/runs/45303702601), 226 PASS / 2 FAIL. Os testes integrados de checkpoint, invalidação e autorização passaram em ambos os bancos; as duas falhas ocorreram no teste de retry seguido de nova mensagem: timestamps iguais e UUID aleatório invertiam a ordem, permitindo geração posterior antes da pendente.
- Correção estrutural: `4227504`, `dc364fb`, `93c6fdc`, `d649f07` introduzem sequência de admissão persistente na tabela candidata e usam essa sequência no desempate FIFO. O mesmo teste conserva timestamps iguais. Não declarar correção validada antes de novo CI.
- Revisão posterior também adiciona injeção de falha dentro da transação após gravação de outbound/audit (75a707f), rejeição de histórico/outbox divergente (5e6ce36), journals na transação (7f97bb2), wake depois da liberação da lease (4308b48) e guarda de autorização da realização/self-choice (c2415dd).
- HEAD de código a validar: `c2415dd28166f5a075b1e471e8e8db29a3cf2618`. Suíte candidata: 31 casos previstos (15 por backend + contrato SQL). CI da revisão ainda não confirmado.
- Limites restantes: ordem causal completa na janela de crash de inputs ainda sem job/ACK, contexto de turnos posteriores já observados, recuperação de efeitos/journals/eventos fora da transação (incluindo appraisal/self-choice), autorização/invalidação concorrente no limite do envio físico, encerramento/reinício real do dispatcher, rollback/migration/versionamento e backup/restore do schema candidato. Completar matriz D01–D16 e medição de recursos antes de promover; web e anexos ficam explicitamente fora do piloto textual.
- Próxima ação: verificar CI atualizado, corrigir regressões, fechar os gates de integração e preparar certificação de produção com schema/registry aprovados, backup/restore, rollout/rollback e único Gateway. Não habilitar em produção nem iniciar RUN 02 antes do gate. Northflank, Altare, dados e configuração ativos intactos.

### RUN 01 — validação da integração e preparação de rollback (23/11/2026)

- Revisão de integração 59dcbec aprovada: 8/8 workflows PASS. [PG18 lab](https://github.com/Sllay/Amora-Chibi/actions/runs/45304308552) 228 PASS; os oito casos integrados passaram nos dois bancos, inclusive FIFO após correção. [Core](https://github.com/Sllay/Amora-Chibi/actions/runs/45304308571) 2.127 PASS / 143 skipped em 3.11, matriz 3.11–3.14 PASS.
- Nova implementação: admissão atômica de input + journal + job antes de ACK (3938f88, 8daca9a, f87b84c, a07c54d), mantendo reflexão reconciliável. Teste de rollback no INSERT/ENQUEUE (c0ae12d).
- Migração PG18 aditiva do componente v1, mantendo base v18 e instalação explícita: a15b56, 96cac38. Revisão estrutural fail-closed no startup PG/SQLite (3103b3d, e73797f, 3bb2c96, 54d0d60, 8ea3881). O instalador sintético usa o mesmo contrato de migração. Não aplicada a produção; revisão integral de constraints/privilégios/backup real ainda necessária.
- Modos off/accept/drain com opt-in explícito e registry PG condicionado; status autenticado sem conteúdo de usuário. Commits b6ea2e8, 33d0e05, 1365f77, d638b12, 9f919e4. Drain mantém trabalho aceito e rejeita novo ingresso elegível; exige janela de manutenção, pois o worker não garante reenvio indefinido de ingresso não aceito.
- Novos testes de drain, schema divergente e aprovação (5ce3d26) e backup/restore nativo em SQLite/PG18 com novo Core, ready + pending preservados, ACK e ordem (58c7e72). CI desse novo conjunto ainda não confirmado. Não confundir 8/8 da revisão 59dcbec com validação da nova revisão.
- [Runbook de rollout e rollback](RUN01_ROLLOUT_AND_ROLLBACK.md), commit bb6c575: backup/restore isolado, preflight, drenagem sem apagar fila, gates de produção. Sem mudança de flags, schema ou implantação ativa.
- Gate atual: CI atualizado, revisar efeitos/contexto causal, recuperação do ACK físico desconhecido, concorrência de invalidação/claim, status e recursos; confirmar backend e SHA efetivos de produção, backup/restore privado e autorização canário Northflank. RUN 01 permanece aberta. Não iniciar RUN 02.

### RUN 01 — revisão de integridade do schema (23/11/2026)

- Revisão 36266a3: CI anterior ainda incompleto no momento da revisão; não reutilizar o PASS de 59dcbec como certificação da nova versão.
- Lacuna encontrada: a revisão PG18 aceitava cinco CHECKs mesmo quando uma expressão era substituída por uma versão mais permissiva. Correção 678ec00: comparar as definições canônicas do catálogo, exigir constraints validadas/enforced e conferir a identidade real da tabela referenciada pela FK.
- Regressão 7edeac7: alterar o CHECK da revisão de transporte de >=1 para >=0 sem mudar o número de constraints; a revisão deve rejeitar o schema. Teste no laboratório PG18 ainda pendente, incluindo confirmação da representação canônica real do catálogo.
- Gate: aguardar evidência de PASS do Core e PG18 no HEAD, corrigir eventuais divergências e registrar contagens efetivas; sem merge, deploy, flags ou migração de produção. Os gates de ACK físico, causalidade, invalidação concorrente, recursos e certificação real permanecem abertos.

### RUN 01 — encerramento do gate de desenvolvimento (24/11/2026)

- HEAD 6163c34202eeffcd1a82a768a69e40e02d6bb913: 9/9 workflows PASS; Core 45311388003, PG18 45311388024. Proprietário aprovou avanço com CI verde e bateria profunda/live após RUN 07. A antiga exigência de live individual está supersedida pelo PLAN.md; preservar pendências de causalidade, ACK físico, concorrência, recursos e rollback real para certificação integrada. PR #378 permanece draft, não integrado em main.

### RUN 02 — início (24/11/2026)

- Status IN_PROGRESS. Branch feat/cognitive-evolution-run02-provider-orchestrator, baseada no HEAD aprovado da RUN 01, sem alterar produção.
- Inventário: provider_resilience.py repete transporte dentro do proxy; resilience_runtime.py e provider_circuit.py mantêm cooldown e half-open; structured_runtime.py faz failover e graça Numes de 12s; durable_reply_runtime.py aplica retry fixo e só conhece causas genéricas; context_budget.py já projeta opcionalmente sem reescrever original.
- Decisão: evoluir esses contratos existentes, preservar adapters/rotas e gates de privacidade. Prioridade: autoridade única de retry durável, classificação de HTTP, Retry-After, compactação corretiva, SSE com progresso e limite finito. Quotas reais e integração dos especialistas precisam de revisão; não inventar limites de conta.
- Gate: testes do novo comportamento + CI integral verde; bateria profunda e live só após RUN 07.

## Modelo obrigatório de registro para cada RUN

Copiar esta estrutura ao iniciar a RUN. Atualizar a tabela de progresso.
Se houver vários PRs, criar entradas datadas no mesmo registro e manter
rastreabilidade de todos os commits, sem apagar a história.

### RUN NN — nome — data
- Status:
- Base de main / schema efetivo / host / flags:
- Issue / branch / PR / commit(s) / CI:
- Objetivo e escopo exato:
- Dependências e critérios de entrada:
- Inventário do código e baseline medido:
- Decisões e ADRs (incluindo alternativas rejeitadas e por quê):
- Contratos/migrations/compatibilidade e segurança:
- Implementação por módulo/arquivo:
- Erros observados e causas confirmadas (separar hipótese):
- Correções realizadas e evidência de resolução:
- Testes executados (comandos, resultados, contagens, links ao CI):
- Testes falhos/não executados e motivo:
- Cenários de integração e falha/restart/dedupe:
- Desempenho antes/depois (p50/p95, tokens, chamadas, CPU/RAM/lag conforme impacto):
- Estado live, revisão realmente implantada, configuração não sensível:
- Rollout/rollback (como retornar sem perda de pendências):
- Riscos residuais, restrições, pendências e responsável:
- Gate: aprovado/reprovado/bloqueado, justificativa e evidência:
- Próxima ação concreta e pré-requisitos:
- Comentário de encerramento na issue/PR:

## Backlog de decisões e mudanças

Novas propostas não substituem o plano silenciosamente. Registrar data,
origem, RUN proprietária, escopo, decisão, motivo, referência e impacto nos
gates. Se não aprovada, manter como proposta; se rejeitada, registrar por quê.
Nenhuma implementação futura deve depender de uma mensagem privada ou de um
arquivo fora do repositório para entender o escopo técnico.
