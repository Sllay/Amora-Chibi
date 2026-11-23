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
| 01 | Durable Cognitive Runtime | IN_PROGRESS — contrato e persistência candidata |
| 02 | Intelligent Provider Orchestrator | PLANNED |
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
