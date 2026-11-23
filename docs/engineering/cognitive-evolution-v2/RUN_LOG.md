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
| 00 | Preparação e continuidade | Documentação registrada nesta revisão; conferir merge |
| 01 | Durable Cognitive Runtime | PLANNED — próxima |
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
Registrar SHA/PR e conclusão do merge no comentário de #376 e na revisão
correspondente; o histórico Git é a evidência de persistência.

**Próximo passo:** iniciar RUN 01 com [RUN01_HANDOFF.md](RUN01_HANDOFF.md).
Não declarar nenhuma das sete RUNs pronta antes da implementação e testes.

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
