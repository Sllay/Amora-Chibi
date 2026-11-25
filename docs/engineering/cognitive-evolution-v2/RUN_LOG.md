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
| 02 | Intelligent Provider Orchestrator | CI_PASS (10/10, bd0d827); cobertura residual registrada |
| 03 | Adaptive Context & Comprehension Engine | CI_PASS (11/11, 978d9f8); escopo residual registrado |
| 04 | Advanced Multimodal Perception | CI_PASS (11/11, cbe211b); escopo residual registrado |
| 05 | Autonomous Web Intelligence | CI_PASS (11/11, a431fb0); escopo residual registrado |
| 06 | Living Conversation Engine | CI_PASS (11/11, 1638748); escopo residual registrado |
| 07 | Cognitive Integration & Certification | IN_PROGRESS |

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

### RUN 02 — incremento A: resiliência de diálogo durável (24/11/2026)

- PR #380 cumulativo sobre 6163c34, inicialmente empilhado e retargeted para main exclusivamente para disparar a matriz completa (Core tests filtra a base); manter draft, sem merge/deploy. HEAD de código 66f88c8.
- Proxy não repete transporte quando o dispatcher é dono do turno (6ea065b, f22eebf); reparo estruturado limitado preservado. 413 recebe causa de input, RemoteProtocolError é transitório, Retry-After não finito é recusado. Compatibilidade do código HTTP_401 preservada por teste existente.
- SSE Numes: heartbeat e último progresso separados (28867e6); janela adicional somente com progresso recente, cap provisório de 120s além da tentativa normal, idle 23s e cancel/join; reserve do orçamento de failover preservada (62e0229). A janela externa do dispatcher permite o cap (be8d624), sem mudar rota síncrona. Valores são candidatos a calibrar, não medições live.
- Classificar conjunto de falhas e não apenas a última: 401/403/404 de todas as rotas candidatas -> blocked; 413 irredutível de todas -> context blocked; falhas mistas/transitórias -> retry_wait. Não abrir circuito por 413. Retry-After propagado e persistido com jitter, teto operacional de sete dias; cabeçalho superior bloqueia para revisão (4a29a22, be8d624, 401bb98, 9bd5501). Cooldown de diálogo respeita cabeçalho longo (66f88c8).
- Projeção estrita de correção: pelo menos 20% menor em estimativa, bytes efetivamente menores, original/identidade/plan intactos; Groq Responses não-stream do piloto durável repete uma única vez se reduzível, sem repetir especialista (a87fac7, 53a06c5). Sem alegar que 413 revela limite exato.
- Testes adicionados: classificação, retry único, compactação, Groq mock, SSE progress/heartbeat, 429 e 401 integrados nos dois bancos (1754593, 6dee2fd, b2a5489, ec98e73). Ainda sem PASS do HEAD. Nenhum provider real chamado.
- Pendente dentro da RUN 02: registro completo de capacidades/limites observados, quotas compartilhadas com visão/web/reflexão e seus gates, half-open adquirido no momento da tentativa, métricas de saúde e liveness dos demais adapters, reparo 413 nos outros caminhos compatíveis, testes integrados de failover e CI completo. Não marcar RUN 02 concluída nem iniciar RUN 03 até fechar esse escopo e obter CI verde. Bateria profunda e produção permanecem após RUN 07.

### RUN 02 — incremento B: regressões e coordenação de conta (24/11/2026)

- CI 211b40b: 8/9 PASS; Core Python 3.14 2.138 passed, 151 skipped, 2 failed; demais versões canceladas pela matriz. Workflow 45429536281; PG18 45429536745 PASS. Falhas: novo teste 413 exigia redução sobre envelope já praticamente mínimo; teste do Visual Engine introduzido na base esperava página pública e middleware respondia 401.
- 1a43c04: permite GET/HEAD exclusivamente nos três caminhos HTML estáticos de /visual-engine, sem liberar APIs privadas, métodos de escrita ou worker. fb0b47c: tenta redução preferencial de 20%, ou redução estrita menor quando o contexto causal mínimo impede o alvo; nunca repetir envelope igual. 40e570e e 268a5ff: fixture genuinamente redutível, teste de redução pequena e proteção do literal.
- cc88c0b, d84af27, f65b104: half-open adquirido no despacho real, não na seleção; mantém exclusão durante toda tentativa, libera também em cancelamento. Teste com relógio simulado cobre tentativa superior à lease anterior.
- 6d69b9b, c02bec0, 60b4046, e5239f5, 204e901, 367d101, 835adb4: coordenador de conta por Core, lock FIFO com espera limitada, cooldown de 429 compartilhado por diálogo, reflexão, visão de entrada Groq/NVIDIA e planejador web Groq. Não assume RPM/TPM reais. Alias conhecido compartilha conta; backoff sem acesso remoto; web retorna consulta determinística. Uma operação concorrente por conta é política conservadora inicial, a calibrar. 5d9c985: testes de cooldown cruzado, independência de contas e cancelamento/FIFO.
- 90ef95a: estado expõe tarefas, suporte estruturado, janela de contexto, saída quando conhecida, privacidade, quota e indicadores de ranking sem credenciais.
- Pendente: confirmar CI neste HEAD, corrigir regressões; verificar cobertura dos demais adapters (incluindo visão de expressões, rotas legadas e SSE não Numes), métricas observadas e testes de integração/failover restantes. Nenhuma quota upstream inventada, nenhum deploy ou merge. RUN 02 ainda IN_PROGRESS.

### RUN 02 — incremento C: quota por modelo Groq (24/11/2026)

- CI ef5a9b8: 9/10 workflows PASS, Core Python 3.13 2.143 passed, 151 skipped, 1 failed; outras versões canceladas. Workflow 45489653707. Avatar 401 e compactação 413 anteriores passaram. Regressão: test_gpt_oss_memory_queue_and_qwen_fallback[True], 429 do GPT-OSS bloqueava Qwen indiscriminadamente.
- Referência pública consultada: https://console.groq.com/docs/rate-limits e https://console.groq.com/docs/projects. Groq publica quotas por modelo dentro da organização/projeto. Não assumir valores numéricos da conta real.
- 68b5990: cooldown indexado por família + modelo para Groq com modelo identificado; sem identificação ou provedor de escopo desconhecido continua global. Bloqueio explícito de conta sempre prevalece. Lock FIFO por conta permanece, sem paralelismo novo nem troca de conta.
- 7990922, decca94, 2a896e9, f3d6e63, fd28651, ace1361: propagação do modelo real na reflexão, diálogo, visão e planner web; diagnóstico fixo quota_scope=account preservado quando explicitamente disponível. Não interpretar cabeçalhos/documentos desconhecidos como sinal de escopo global.
- 1d400db: regressões de 429 por modelo, mesma rota compartilhada em diálogo/visão/web, fallback entre modelos distintos, bloqueio global e expiração independente. O teste integrado original GPT-OSS → Qwen permanece inalterado. Aguardar CI do novo HEAD para considerar o gate de desenvolvimento aprovado. Sem deploy ou merge.

### RUN 02 — checkpoint de desenvolvimento e avanço autorizado (24/11/2026)

- bd0d82707655f4b156995b23d3ae59f739b1a0d5: 10/10 workflows PASS; Core 45494076737 aprovado em Python 3.11/3.12/3.13/3.14, PG18 45494076903, Provider certification 45494076778. PR #380 draft e mergeable=false na consulta; nenhum merge ou deploy.
- Proprietário autorizou iniciar RUN 03 após CI verde. Não declarar cobertura integral de adapters: revisão de visão de expressões, rotas legadas, SSE não Numes, recuperação 413 adicional e métricas de saúde continuam como trabalho residual de integração (RUN 04/07 e respectivos módulos). Não confundir com certificação live; corrigir regressões conhecidas que afetem RUN 03 antes do gate.
- Branch seguinte mantém ancestralidade do HEAD validado. Produção intocada.

### RUN 03 — início: Adaptive Context & Comprehension Engine (24/11/2026)

- Branch feat/cognitive-evolution-run03-adaptive-context, base bd0d827. Objetivo: contexto adaptativo, orçamento de envelope por rota, AMD por offsets, mapa de cobertura e consolidação sem alterar texto original; preservação de identidade/ACIR e memória.
- Inventário inicial: context_v1.py compila contexto e capabilities; context_budget.py estima JSON por caracteres/4 e compacta projeções opcionais; prompt_frame.py separa protocolo confiável e fala literal, inclui ACIR no turno e no plano; structured_runtime.py controla failover e realização validada. Revisar context.py, adapters e testes antes da integração.
- Decisão: caminho curto sem chamada adicional; AMD só para entrada que não cabe mesmo após compactação, limite finito de blocos e evidência explícita de cobertura. Não transformar heurísticas em fatos ou persistir resumos como memória. Fase inicial: contratos determinísticos, telemetria numérica e fixtures de negação tardia; integração semântica e gates completos ainda pendentes.
- Gate CI integral após implementação; bateria profunda depois da RUN 07.

### RUN 03 — incremento A/B: AMD, integração do Avatar e compatibilidade (24/11/2026)

- PR #382 draft, base main e cadeia RUN 01/02 preservada. HEAD 4907205 ficou sem CI: main e56453b evoluiu em paralelo com Avatar/Visual Engine; conflito confirmado somente em core/amora/security.py. Merge 99bceef, dois pais e sem force-push, copiou os blobs originais de main (routes, testes, HTML e README) e manteve CSP com hash específico, leitura pública somente dos três caminhos HTML, sem liberar subpaths/API/worker. Nenhuma alteração em main ou produção.
- 99bceef: CI 9/10; Core 45523727451, Python 3.11 2148 passed, 151 skipped, 5 failed (demais cancelados). Quatro falhas em test_phase3_references: protocolo de Mistral exige ACIR inline no DialoguePlan; compactação universal alterou o contrato. Uma em test_security_headers_cors_and_minimal_probes: teste antigo exigia script-src self exato até no HTML que precisa do hash. Correção neste commit: compactação ACIR opt-in apenas na realização Groq, instrução explícita de resolução same_as; representação legada e semântica canônica intactas. Testes de CSP preservam política estrita nos demais caminhos, verificam igualdade do conjunto de hashes de script e bloqueio de subpath.
- e843190, 7d059e4, df542b8, 1029315, 92e7fdd, 90ab1fb, 742bcc6, f5b242d, 4907205: AMD por offsets Unicode e limite UTF-8, reconstrução literal e sinais lexicais; orçamento de envelope por janela conhecida e reserva de saída, limite de bytes se conhecido, sem inventar quota/tokenizer. Primeiros testes passaram salvo incompatibilidades acima. Telemetria existente de payload é por bytes e estimativa, não contagem de tokenizer.
- 99ae585, 859ddcf, 017b828, d5b3004: protótipo integrado ao piloto durável textual: só depois de todas as rotas elegíveis falharem por contexto (sem candidato em cooldown); não há chamada adicional no caminho normal. Máximo de seis blocos de 4096 bytes e overlap de 160 caracteres, análise estruturada Groq com citações exatas ligadas aos offsets, validação de cobertura de marcadores consequenciais, agrupamento global obrigatório de todos os IDs, realização final pelo gate existente. Fonte permanece no Core; projeção efêmera PrivateAttr não serializada nem escrita como memória; interpretações não canônicas. Autorização durável renovada antes de cada chamada, sem preview, mesmo lock de conta e orçamento externo; interrompe se relatório de quota esgotada. Estimativas e política de limites ainda experimentais; resposta pode ser bloqueada se mapa insuficiente, não truncar silenciosamente.
- Testes: corpus sintético A/B com negação tardia e pergunta visual, limites Unicode, rejeição de citação inventada e agrupamento incompleto, mock de todas as etapas, autorização revogada, integração SQLite/PG18 (entrada original e resposta única, AMD só no overflow). CI do HEAD novo ainda não confirmado; nenhum provedor real consultado.
- Pendências de RUN 03: recuperação de overflow anterior à seleção de provider (compiler), decomposição opcional de subtarefas e geração por partes quando necessária; leitura sob demanda adicional de trechos, integração visual real pertence à RUN 04; ampliar corpus, comparação quantitativa de tokens/latência/qualidade (meta 30–50% somente quando houver redundância). A validação de cobertura lexical/quotes não prova compreensão semântica correta. Não marcar RUN 03 concluída; sem certificação live, deploy, merge em main ou monitoramento ChatGPT.

### RUN 03 — incremento C: consultas de memória com tokens extensos (24/11/2026)

- 171c942: CI 10/11 PASS; Core 45524181148 PASS e Pre-deploy security 45524181149 PASS, confirmando correções de compatibilidade ACIR/CSP. PG18 45524181128: 244 passed, 1 skipped, 2 failed, ambos em test_run03_durable_adaptive_only_after_full_context_rejection[pg18], com e sem overflow simulado. O dispatcher registrou BM25LabError antes da realização e conservou o turno em retry_wait.
- Causa localizada: normalized_fts5_terms rejeita termo >128 caracteres; fixture original contém um token contínuo de 4400 caracteres. O limite de busca lexical não deve impedir processamento da mensagem completa. Também há caso sem termos lexicais. Não há evidência de corrupção, indisponibilidade de Aiven real ou falha de schema.
- Correção: projeção lexical compartilhada em MemoryStore (até 16 termos válidos com <=128 caracteres, sem fatiar tokens longos); SQLite e PostgreSQL usam a mesma seleção antes de ranking. Normalização unicode61 mantém contrato estrito do laboratório por padrão, caminho runtime explícito retorna vazio se não houver termo elegível. Preservar fonte, filtros de privacidade, baseline de candidatos e recall exato do proprietário; sem alteração de schema, índice, conteúdo de memória ou permissões. O teste integrado longo permanece intacto; acrescentado teste de limite, acentos, termo opaco, ausência de termos e cap.
- Novo CI necessário; RUN 03 ainda IN_PROGRESS, sem produção e sem monitoramento.

### RUN 03 — checkpoint de avanço autorizado (24/11/2026)

- 978d9f87fd1c63c1c82d5598bc6133edb4fb8fe0: 11/11 workflows PASS; Core 45526184528 Python 3.11–3.14 PASS; PG18 45526184127 PASS. PR #382 mergeable=true, draft, sem deploy. Proprietário autorizou avanço para RUN 04 após consulta.
- CI aprovado não significa escopo completo: overflow no compilador, leitura adicional sob demanda, geração por partes (interface RUN 06), corpus e medições quantitativas permanecem residuais e devem ser fechados antes do gate final. Não ocultar limitações.

### RUN 04 — Advanced Multimodal Perception — início (24/11/2026)

- Base 978d9f8; branch feat/cognitive-evolution-run04-multimodal-perception. Escopo oficial seção 7 do PLAN. Inventário inicial: incoming_vision.py Groq e cascade; nvidia_incoming_vision.py opt-in; visual_context.py schema limitado; messaging/models.py anexo singular e validação CDN; worker, ingress, replay e store a revisar. Falhas atuais: corte [:500] do foco, único anexo, observação sem identificação de origem/versionamento e causa genérica.
- Primeira etapa: foco adaptativo preservando mensagem inteira quando cabe, leitura da cauda e perguntas tardias, perfis de orçamento, códigos de falha e metadados de fonte com compatibilidade; mapear múltiplos anexos sem ampliar autorização nem tamanho total cego. Reusar quota RUN 02, não enviar imagem ao provider textual. Não persistir pixels ou URL assinada. Validar com mocks e CI; live Discord depois da RUN 07. Sem monitoramento.
- Próximas etapas: contratos de múltiplos anexos ponta a ponta, cache/checkpoint vinculado a revisão/consentimento e fonte, retry sem repetição de visão concluída, isolamento, dependências visão/web, corpus e desempenho. Não declarar COMPLETED sem entregar escopo.

### RUN 04 — incremento A: foco visual integral e diagnóstico (24/11/2026)

- PR #384 draft, início f85fa73, baseado no 978d9f8 validado (11/11). Main avançou em paralelo apenas em README/HTML do Avatar no momento da criação; não sobrescrever esses blobs.
- e8a7abe: removido corte [:500] de ambos os adapters de visão. Contrato visual_focus.py recebe legenda literal inteira até o limite existente do InboundMessage (12000 caracteres), rejeita excesso explicitamente; classificador determinístico rápido/direcionado/detalhado controla foco, não permissões ou significado. Nenhuma chamada extra para classificação. Qwen/NVIDIA preservam MIME/bytes, consentimento, fallback opt-in e teto temporal. Diagnósticos fixos diferenciam quota, timeout, recusa, observação inválida, transporte e formato; nunca registram conteúdo ou URL. Realização responde ao texto independente, explicita parte visual indisponível.
- 4f8796b: modo detalhado reserva 2400 tokens de saída em vez de 1400 e admite descrição 1200 caracteres, 16 trechos OCR e 8 incertezas de até 240 caracteres; limites são configuração experimental local, não capacidade certificada do provedor. Modo rápido/direcionado mantém contrato anterior. Observação detalhada v2 inclui provider/model gerados pelo Core. Modelo do sensor continua não canônico; schema legado preservado e validado; nenhuma imagem é encaminhada ao modelo textual. Testes mock de ambos os adapters verificam pergunta tardia, Unicode, OCR ampliado, isolamento, limite de foco e códigos de erro; teste integrado de falha de transporte atualizado para causa específica, sem remover garantia de preservação de legenda.
- Gate pendente: CI do HEAD ainda não certificado, sem chamadas reais. Múltiplas imagens, vínculo estável anexo/revisão, validade, cache/checkpoint persistente e retomada, política de leitura de regiões, dependências visão/web, telemetria quantitativa, cobertura de edição/exclusão e revogação ainda não implementadas. Próxima etapa deve auditar worker.mjs, core-client.mjs, serviço de ingress, storage e limites HTTP antes de ampliar payload. Não declarar conclusão da RUN 04. Sem deploy ou monitoramento.

### RUN 04 — checkpoint de avanço autorizado (24/11/2026)

- cbe211bc207e33845436af6ae41a402c0e563758: 11/11 workflows PASS, Core 45527660870, PG18 45527660874; PR #384 draft, mergeable=true. Proprietário autorizou iniciar RUN 05. Escopo residual de múltiplos anexos, vínculo e validade por revisão, checkpoint de visão e integração visual permanece aberto para integração; CI não equivale a conclusão do escopo.

### RUN 05 — Autonomous Web Intelligence — início (24/11/2026)

- Base cbe211b, branch feat/cognitive-evolution-run05-web-intelligence; seção 8 do PLAN. Inventário: navigator.py, query_planner.py, workspace_store.py, remote_reader.py, browser_client.py, structured_runtime.py e web_delivery. Caminho atual para no primeiro motor relevante, lê duas fontes e só usa fallback remoto se ambas falham. Limites externos podem cancelar sem resultado parcial.
- Primeira etapa: perfil aprofundado explícito separado do rápido, consultas alternativas relevantes, fontes de domínios variados, fallback por página e deadline cooperativo que devolve evidência parcial. Nenhum motor novo ou serviço pago presumido. Preservar autorização owner-DM, SSRF, URLs sensíveis, provenance e separação de evidência/instrução.
- Próximas etapas: checkpoint/replay persistente por turno e sessão, avaliação semântica de evidência e contradições, continuidade de pesquisas e entrega confiável de screenshots, testes de cancelamento/isolamento e medição de custos. Não declarar cobertura total. Sem produção, sem monitoramento.

### RUN 05 — incremento A: pesquisa aprofundada limitada e evidência parcial (24/11/2026)

- PR #385 draft, branch empilhada sobre cbe211b (11/11). Início 3d6bbed. e8abecb e 6240e20 implementam perfil deep explícito (pesquise a fundo, em profundidade, cruzar fontes), intenção contextual de verificação na web, caminho rápido preservado, alternativas determinísticas de consulta sem chamada adicional de modelo, máximo 4 buscas, 10 referências, 5 leituras, 1500 caracteres por fonte no envelope, deadline interno 14s (descoberta até 5,5s), concorrência 2. Limites são locais experimentais, não quotas certificadas. Mantém resultados completos antes de timeout, cancela awaiters, distingue encontrados/lidos, checa relevância contra assunto original e diversifica hostnames (não prova independência).
- Fallback Reader/browser por página no modo aprofundado, sem reencaminhar falha explícita de rede privada/URL inválida ao renderer; URLs compartilhadas passam filtro de parâmetros sensíveis. Transporte existente de DNS/IP pinning e redirects permanece. Correção em _call: mantém slot do thread de HTTP/DNS até saída real, mesmo quando awaiter cancela; não promete interrupção imediata de socket. Core mantém orçamento externo de 16s para deep, sem novo serviço e sem browser permanente.
- Notebook v18 existente registra até cinco fontes abertas no modo deep e provenance disponível, referências ordenadas, sem DDL. Instrução de realização exige distinguir resultados e páginas lidas, comparar evidências, explicitar divergências e incompletude. Nenhuma confirmação automática de fatos/contradições. Testes sintéticos: consulta contextual/rápida, diversidade, fallback 403 por página, isolamento notebook, resultado parcial sob deadline, rede privada negada, slot durante cancelamento. Ainda não há resultado CI do novo HEAD confirmado.
- Pendências: checkpoint por turno recuperável durante execução/restart, continuidade sem repetir consultas e leituras já concluídas, TTL/validade e revogação, avaliação semântica de contradições, pesquisa dinâmica com planejador e evidência visual, tratamento de quotas por motor, auditoria de todos os caminhos de SSRF (inclusive vendor e redirects), revisão da entrega de links/prints além dos limites existentes do Discord, testes de precisão e medidas p50/p95. Nenhum teste real de endpoints externos, deploy, alteração de produção ou monitoramento. RUN05 permanece IN_PROGRESS; bateria final após RUN07.

### RUN 05 — checkpoint de avanço autorizado (24/11/2026)

- a431fb0cde7bab5453cf9ccdf960b7636d78a591: 11/11 workflows PASS, Core 45528543201 e PG18 45528543164 PASS; PR #385 draft, mergeable=true. Proprietário autorizou RUN06. Retomada persistente, screenshots, verificação semântica e demais pendências anteriores continuam abertas para integração.

### RUN 06 — Living Conversation Engine — início (24/11/2026)

- Base a431fb0, branch feat/cognitive-evolution-run06-living-conversation. Seção 9 PLAN. Inventário: durable_reply_runtime.py, reply_jobs.py/SQL e geração já preservam IDs, ordem da fila e ACK; ContextCompiler lê últimas 96 mensagens sem separar mensagens aceitas posteriormente da fala atual; SpeechSegmenter e gate de realização existentes. Não alterar rig/voz ou promover execução parcial não validada.
- Primeiro incremento: delimitar contexto causal do piloto de texto, identificar continuidades e correções de forma conservadora, preservar mensagens independentes e as identidades originais, introduzir janela curta apenas em sinais explícitos de continuação. Testes sintéticos e compatibilidade SQLite/PG. Não suprimir respostas aceitas, nem marcar agrupamento semântico/retomada de segmentos como entregue sem transação e contrato de ACK.
- Próximas etapas: agrupamento transacional/replanejamento antes de commit, supersessão, retomada e ACK de segmentos, barge-in/voz/avatar, métricas de TTFS. Não realizar deploy nem monitoramento.

### RUN 06 — incremento A: snapshot causal e janela curta (24/11/2026)

- PR #386 draft, checkpoint 86c00d5; base validada RUN05 a431fb0 (11/11). 5bf2b5f e 12dd821 implementam separação de entradas posteriores do histórico do turno durável. Reusa leitura session-scoped e exclude_invalidated existente, ancora no registro canônico, apenas owner DM remoto autorizado; ordena por timestamp observado, sem inventar ordem em empate. Se âncora ausente, omite histórico ambíguo. Correção/complemento explícito e lexicalmente relacionado até 8s pode entrar como evidência separada, máximo 3 mensagens e 1800 caracteres integrais, parando em novo assunto/interrupção/ambiguidade; não reescreve texto nem IDs. Classificador é heurístico conservador, não compreensão semântica comprovada.
- Janela experimental de até 650ms apenas para primeira tentativa com sinais de frase incompleta; não atrasar saudação ou pergunta normal. Revalida lease/autorização depois da espera. TurnFrame marca followups como dados não canônicos e realização orienta não contradizer correção explícita; mensagens posteriores preservam jobs independentes. Sem alteração de schema/voz/rig, sem afirmar entrega ou cancelar respostas por heurística.
- Testes unitários de ordem, ambiguidades, privacidade, preservação literal, janela; integração SQLite/PG18 verifica três mensagens aceitas, correção projetada separadamente, novo assunto excluído, primeiro ready e posteriores pending. CI ainda não confirmado.
- Pendências: snapshot acima cobre apenas mensagens já persistidas no momento da compilação, não replaneja geração em andamento; agrupamento e supersessão atômicos com ACK, detectar interrupção sem marcador lexical, empate com ordem real da fila, filtrar memórias posteriores e distinguir histórico de fala entregue, entrega e recuperação por segmento, TTS/barge-in/avatar, TTFS e testes reais. Não declarar RUN06 completa; manter escopo residual para integração antes da certificação. Sem deploy/monitoramento.

### RUN 06 — checkpoint de avanço autorizado (24/11/2026)

- 163874897a2b25b6219f029e2dc47a6856ff714a: 11/11 workflows PASS, Core 45529615301, PG18 45529615405; PR #386 draft mergeable. Proprietário autorizou RUN07. Pendências do snapshot causal, supersessão e ACK de segmentos permanecem abertas.

### RUN 07 — integração e certificação — início (24/11/2026)

- Base 1638748. Primeira etapa: inventário de escopo residual, diagnóstico correlacionado sanitizado e métricas limitadas do dispatcher; preparar matriz de certificação e teste transversal. Não remover caminho legado nem ativar fila, trocar banco/host, ou alterar voz/Avatar sem equivalência.
- Gate final exige fechar pendências de implementação, CI integrado, bateria profunda e certificação Northflank. 11/11 de cada checkpoint não comprova produção. Alvo 512 MB/0,2 vCPU, sem promover automaticamente. Sem monitoramento ChatGPT.

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
