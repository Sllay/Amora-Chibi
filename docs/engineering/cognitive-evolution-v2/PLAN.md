# Amora Cognitive Evolution V2 — programa de engenharia (RUN 00–07)

**Decisão de produto:** aprovada para planejamento pelo proprietário em 23/11/2026.
**Status:** especificação de trabalho; RUN 00 documental, RUN 01–07 planejadas.
**Escopo:** confiabilidade, cognição contextual, percepção, web, diálogo e integração.
**Issue de origem:** [#376 — Durable Discord reply recovery](https://github.com/Sllay/Amora-Chibi/issues/376).
**Diário obrigatório:** [RUN_LOG.md](RUN_LOG.md).

Este documento preserva as dez propostas discutidas com o proprietário e a divisão
aceita em sete RUNs. É um programa transversal de engenharia, **não** um novo
número de release do produto nem uma declaração de que essas capacidades já
existem. Não substitui o canon de identidade, segurança e comportamento. Em
conflito, seguir a precedência de AGENTS.md e registrar ADR explícito antes de
alterar o canon. Atualizar este plano quando uma decisão aprovada o modificar.

## Política de gates — decisão do proprietário de 24/11/2026

Esta seção prevalece sobre exigências de live/certificação individual nas
seções históricas e handoffs. RUN 01–07: implementar escopo, executar todos
os workflows aplicáveis no HEAD, corrigir falhas, documentar e avançar com
100% verde. Não desabilitar testes, não ocultar skips/falhas, não marcar
escopo incompleto como entregue. A bateria profunda transversal (carga,
causalidade, concorrência, restart, backup/restore/rollback, recursos,
bugs ocultos e certificação live Northflank) ocorre após a implementação
da RUN 07, com o sistema integrado. CI_PASS é gate de desenvolvimento,
não LIVE_CERTIFIED. Bugs conhecidos que impeçam o escopo atual continuam
bloqueantes. Sem deploy intermediário; manter a cadeia de branches/PRs
até integração segura. Registrar cenários adiados e evidência final.

## 0. Como continuar sem o chat original

1. Leia AGENTS.md, docs/canon/{SKILL,ARCHITECTURE,GDD}.md, docs/roadmap/VERSIONS.md,
   PROJECT_STATE.md, ADRs pertinentes e este documento inteiro.
2. Leia RUN_LOG.md, a issue #376, PRs e resultados de CI ali referenciados.
3. Confira HEAD de main, schema efetivo, flags, implementação e ambiente reais;
   checkpoints históricos podem estar desatualizados. Não restaurar solução
   rejeitada sem consultar ADR. Não supor que o deploy usa HEAD.
4. Selecione a primeira RUN cujo gate não esteja fechado, reproduza o baseline
   e registre o plano e os arquivos afetados no diário **antes** de modificá-los.
5. Implemente a RUN em branch/PR com mudanças compatíveis e rollback. Registre
   decisões, evidências, falhas, correções e pendências na mesma revisão.
6. Diferencie IMPLEMENTED/CI_PASS de LIVE_CERTIFIED; não avance sobre um ciclo
   incompleto sem decisão explícita e avaliação de risco. Não dependa de memória
   de chat, de um agente específico ou de promessa de execução futura.

## 1. Motivação e evidências (não confundir com hipóteses)

Amora é companheira persistente, não um chat stateless nem um LLM. A experiência
desejada é conversar continuamente, inclusive texto longo, várias mensagens,
imagem e web. Falhas transitórias não devem terminar uma mensagem aceita.
O proprietário prefere uma resposta atrasada e recuperada a um erro definitivo
seguido de silêncio. Não se promete disponibilidade infinita de serviços externos:
cancelamentos, autorização, limites de recursos e erros permanentes têm estados
explícitos e recuperação controlada.

Incidente Northflank 23/11/2026 (issue #376, log fornecido pelo proprietário):
- Numes/DeepSeek: HTTP 200, 44,43 s até conteúdo, 200 eventos SSE, 103 deltas
  visíveis, 356 caracteres; maior intervalo SSE 2,758 s. Caller cancelou stream
  ativo em 87,0 s (75 s + graça de 12 s). Não é evidência de desconexão upstream.
- Groq/Qwen: HTTP 413, envelope 49.081 bytes / 12.173 tokens estimados; ~267 ms.
  Não se conhece o limite exato atingido (TPM, contexto ou outro limite).
- Gemini: ~2,73 s restantes, timeout. OpenRouter: nenhuma tentativa.
  Budget global 90 s esgotado; fallback operacional entregue em ~97,98 s.
- Outro turno: Numes completou em 45,47 s, turno ~54,39 s.
- Reflexão do turno falho aceitou dois candidatos; mensagem observada antes de
  geração. Isso não prova recall integral do turno seguinte.
- Lag máximo do event loop ~3,91 s no turno falho e ~3,76 s no bem-sucedido:
  investigar CPU/IO e fila separadamente do provedor. SearchParityError da
  iniciativa temporal não foi demonstrado como causa desse incidente.
- Envelopes Numes: estimativas de 8.387 tokens (sucesso) e 10.131 (cancelado).
  São estimativas caractere/4, não tokenizer real. Não culpar exclusivamente o
  tamanho da mensagem: perfil, instruções, memórias e histórico também ocupam espaço.

Baseline observado no código antes deste plano (revalidar em cada RUN):
| Área | Arquivos / comportamento observado |
|---|---|
| Entrada/entrega | core/amora/messaging/service.py; receive protegido por shield, observe_input_async antes de geração/reflexão; orçamento tentativa 75 s, total 90 s, cognição 105 s; fallback operacional pode encerrar turno |
| Ownership | messaging/generation_lifecycle.py; lease por turno 180 s, staging de outbound e recuperação de resposta preparada, não uma fila persistente de geração; messaging/storage_lane.py; stores SQLite/Postgres |
| Transporte | workers/discord/worker.mjs; workers/common/core-client.mjs; inbound HTTP 120 s texto / 160 s imagem; outbox e nonce/ACK existentes; não confundir ACK de transporte e sucesso semântico |
| Geração | cognition/structured_runtime.py; Numes ganha apenas uma extensão de graça, valida JSON completo antes de falar; providers_numes.py (SSE, max_tokens 1200 na configuração observada) |
| Resiliência | cognition/provider_resilience.py, resilience_runtime.py; já existem retries/backoff/cooldown; Numes sem retry transparente de transporte; HTTP 413 não recebe compactação corretiva automática |
| Contexto | cognition/context_budget.py, context.py, prompt_frame.py, realization.py, providers_identity.py; V3 pode usar teto soft 12.288 e hard 16.384 estimados; projeções existentes preservam fala original, mas ainda geram envelopes grandes |
| Memória | messaging/reflection_queue.py; trabalho persistente separado; reusar dedupe, provenance, ACIR e Memory Write Gate |
| Visão | messaging/modality_service.py, incoming_vision.py; especialista antes do diálogo; foco apenas primeiros 500 caracteres; descrição 650 caracteres, até 8 trechos de 120, limite de etapa ~27 s; texto longo completo ainda disponível à cognição social |
| Web | web_tools/navigator.py, query_planner.py, remote_reader.py; até 2 consultas planejadas, busca para no primeiro conjunto relevante, lê 2 páginas em busca normal, 2.400 caracteres por fonte, HTTP direto 420 KB, 3 redirects, concorrência direta 2, Reader 1; budget global na cognição ~16 s com Reader/browser (~11 s sem) |
| Prints | workers/discord, docs/engineering/AMORA_WEB_DISCORD_LINKS_SCREENSHOTS.md; Microlink remoto, default 4 novas capturas/dia UTC por processo; links anexados pelo transporte quando verificados |

A configuração de produção efetiva, limites de conta, disponibilidade de
modelos e latência atuais precisam de confirmação. Não incluir conteúdo privado
da conversa, credenciais, payloads brutos ou dados pessoais no repositório.

## 2. Arquitetura alvo e limites

Northflank é o host operacional nesta frente; orçamento de projeto observado
0,2 vCPU / 512 MB, custo R$ 0 sempre que praticável. Aiven PostgreSQL 18 é
o backend persistente a certificar, confirmar modo efetivamente ativo.
SQLite permanece suportado para local/testes. Altare fica fora do escopo
enquanto a infraestrutura não estiver estável. Não migrar host/banco junto
deste programa. Não instalar Chromium permanente, Redis, Temporal, LangGraph
server ou outro serviço pesado só para copiar um padrão. Usar asyncio, storage
existente, migrações revisadas, gates do Core e provedores remotos sob demanda.
Manter um único Gateway Discord ativo.

```text
Platform adapters: Discord | Web | voz | Android
       |
ingress -> autorização/identidade -> registro original durável -> ACK técnico
       |
orquestrador de turnos (Core; estado/checkpoint/retry/cancelamento)
       |
compreensão global -> plano de dependências -> especialistas necessários
       |                                 memória / visão / web / ferramentas
       v
integração de evidência -> compilador de contexto por provedor
       |
router + recuperação -> realização -> validação progressiva quando certificada
       |
outbox existente / entrega / ACK -> continuidade de diálogo, voz e avatar
       |
reflexão/memória com provenance e dedupe (independente, gates existentes)
```

Um único orquestrador de execução é dono do ciclo de vida, não da identidade,
emoção, memória ou políticas de ação. Cada domínio continua dono da sua
semântica. Não criar segundo sistema de memória, personalidade, scheduler,
router, transporte, log canônico ou motor de autorização. A fonte de estado
persistente é o backend ativo do Core, com domínios separados por contrato.
Fluxo normal curto: nenhuma decomposição, planner remoto, web ou visão sem
necessidade. Uma etapa conceitual não implica uma chamada LLM.

### 2.1 Contratos lógicos propostos (nomes não são schema aprovado)

- **TurnEnvelope:** turn_id determinístico existente, sessão, referência à
  mensagem original íntegra, ordem/revisão, ator resolvido, plataforma, consent
  e escopo, referências autorizadas a anexos; não serializar secrets, bytes de
  imagem brutos, URLs assinadas ou conteúdo AUTH/SECRET em fila/log.
- **TurnJob:** estado, etapa, attempt_count, next_attempt_at, lease_token,
  lease_until, versão/checkpoint, cancelamento, causa operacional e referência
  de entrega. Persistir somente dados permitidos necessários para retomar. Estados
  lógicos accepted/pending/running/retry_wait/ready/sent/cancelled/blocked;
  ready não é sent; sent depende de ACK de entrega. Definir transições e invariantes
  em RUN 01, sem criar schema paralelo a tabelas existentes sem inventário.
- **TurnPlan:** IDs de trechos originais e seus offsets, intenções/perguntas,
  relações entre tópicos, correções/negações, dependências, evidências necessárias
  e ordenação. Mapa auxiliar não substitui original nem vira memória canônica.
- **StageResult:** status, source/revision, evidence refs, timestamp, versão de
  schema, validade/TTL, erro classificado, retryability; checkpoint só depois de
  validação e persistência segura. Reutilizar resultado válido; revalidar se
  edição, exclusão, nova evidência, consentimento ou TTL invalidarem.
- **ProviderAttempt:** task class, provider/model, capacidade e quota conhecida,
  estimativa de tokens/bytes e reserva de saída, deadline de conexão/idle/segurança,
  métricas, classe de falha e próxima ação. Sem payload bruto em diagnósticos.
- **ReplySegment:** turn_id + índice/versão, resposta semanticamente completa
  e validada para aquele segmento, refs de dependência, status de entrega,
  idempotency key. Não liberar JSON parcial ou raciocínio interno. RUN 06
  introduz progressão somente se compatível com gates e contratos de voz/texto.

### 2.2 Invariantes transversais

1. Original aceito não é perdido por falha de modelo. Preservação respeita
   política de privacidade, retenção, revogação, edição e exclusão.
2. ACK de ingresso rápido após gravação durável/reconciliável; não afirma
   conclusão. HTTP/worker não determina a duração total do trabalho cognitivo.
3. Um turno lógico tem uma resposta final coerente. Segmentos permitidos são
   partes dessa mesma resposta. Retry não cria duplicata de reflexão, memória,
   ação, segmento ou entrega. Idempotência e fencing/lease com expiração e
   renovação; não prometer exactly-once físico de rede: usar dedupe + ACK.
4. Ordenação causal por sessão, paralelismo limitado entre sessões; aceitar
   novas mensagens durante espera, não descartar. Cancelamento explícito,
   invalidation de revisão e revogação de consent têm precedência. Evitar
   starvation; definir política de continuidade/agrupamento na RUN 06.
5. Falha recuperável gera próximo passo persistido, não fallback social terminal.
   Aviso técnico opcional único por janela, sem virar fala/memória da personagem.
   Falha permanente bloqueia com causa e opção de retomar após correção.
   Sem retry infinito agressivo, custo involuntário ou provedor não autorizado.
6. Texto original não é truncado silenciosamente; reduzir projeções opcionais
   primeiro. Preservar identidade, ACIR, perspectiva, autorizações, causalidade,
   mensagem atual e evidência essencial. Quando impossível, decompor/controlar
   orçamento ou bloquear explicitamente, nunca inventar resposta completa.
7. Não confiar em instruções vindas de web/imagem, respostas de provider ou
   conteúdo não confiável. Preservar proteção SSRF, privacidade, consentimento,
   isolamento, autorização de ferramentas e action gates. Sem bypass de
   CAPTCHA, autenticação ou restrições do site; nenhuma ação externa inferida
   pelo texto da página. Dados externos não são fatos/memórias por si.
8. Nenhuma fala social atribui resultado a ferramenta que não executou. Não
   inventar fontes, prints, detalhes visuais ou lembranças. Diferenciar achado
   de busca, página lida, observação e inferência.
9. Providers substituíveis: perfil/identidade e semântica local do Core não
   mudam com fallback. Preservar identidade vocal congelada, destinos e barge-in.
10. Não deixar auto-reparo modificar código de produção sozinho. Recuperar
    estados operacionais conhecidos; mudanças de software exigem revisão/CI.

## 3. Dez ideias preservadas e como se encaixam

| ID | Ideia | RUN primária / integração |
|---|---|---|
| I01 | Contexto adaptativo por provedor | 03, suporte 02 |
| I02 | Adaptive Message Decomposition: dividir só quando necessário, compreensão global e texto original | 03 |
| I03 | Resposta progressiva, segmentos completos validados | 06 |
| I04 | Pipeline de especialistas com dependências e paralelismo limitado | 01/04/05/06 |
| I05 | Percepção visual rápida/direcionada/detalhada | 04 |
| I06 | Orquestrador de provedores, quota, liveness, failover | 02 |
| I07 | Checkpoints e retomada somente do trabalho incompleto | 01, todas as RUNs |
| I08 | Web cognitiva rápida/aprofundada e evidências reutilizáveis | 05 |
| I09 | Continuidade natural, mensagens complementares e interrupções | 06 |
| I10 | Observabilidade, diagnóstico e autorrecuperação | todas, certificação 07 |

## 4. RUN 01 — Durable Cognitive Runtime (fundação)

Dependência: RUN 00. Começar por inventário do inbound, audit, generation lease,
outbox, reflection queue, source invalidation, scheduler, storage lane,
lifecycle de startup/shutdown e contratos HTTP/Discord. Definir ADR e migration
compatível antes de schema. Aproveitar estruturas existentes em vez de duplicá-las.

Entregáveis:
- Separar aceitação/registro e execução de resposta em DM autorizada inicialmente.
  ACK de fila sem fingir resposta concluída; compatibilidade de adapter e flag
  de rollout. Manter fluxos de controles locais, grupos, Web/voz intactos até
  integração explícita. Reconciliar janela de crash entre gravação e enqueue.
- Fila durável no backend ativo; estados/transições, atomic claim/fencing,
  renovação de lease, retry_wait com next_attempt_at persistido, cancelamento
  e recuperação após restart. Nenhum lock/transaction durante chamada externa.
- Reusar turn_id e inbound dedupe, original já persistido, outbox/ACK e
  reflection dedupe. Checkpoint mínimo para entrada, preparação e resposta
  validada; contratos extensíveis para etapas futuras, não stubs fingindo execução.
- Dispatcher leve, concorrência inicial conservadora (uma geração ativa por
  sessão, limite global inicial baixo; testar e tornar configurável). Trabalho
  atrasado não bloqueia recepção nem polling de entrega. Fila limitada por
  política explícita e backpressure sem apagar aceites.
- Estados de progresso, status técnico distinto de diálogo e tratamento
  temporário de geração falha como pendente. Não ativar recuperação de
  imagem sem mecanismo autorizado; RUN 04 completa reidratação visual.
- SQLite e PostgreSQL PG18; usar migrations e SQL revistos, não fallback
  silencioso para banco diferente. Testar backup/restore e rollback com
  trabalhos pendentes, compatibilidade entre versões.

Gate: matar processo durante geração e em cada janela registro/enqueue,
claim/checkpoint/outbox/ACK; reiniciar e entregar um único resultado lógico,
sem perder input/memória. Repetir inbound, editar/apagar durante processamento,
simular lease vencido, rede HTTP cortada, novas mensagens e sessões independentes.
Teste de histórico seguinte incluindo mensagem não respondida. PostgreSQL real
de laboratório + SQLite; worker e Core integrados; live Northflank separado.
Não declarar retry inteligente completo antes de RUN 02.

## 5. RUN 02 — Intelligent Provider Orchestrator

Dependência: RUN 01. Inventariar provider_resilience, ModelRouter, quotas, cooldown,
task class, SSE e limites de cada adapter. Unificar autoridade de retry; evitar
multiplicação por proxy interno + router + job. Numes pode continuar preferencial
no diálogo se medições justificarem; Groq memory/visão e outros especialistas
preservados. Nenhuma troca de modelo/rota paga sem autorização.

Entregáveis:
- Registro de capacidade por modelo (modalidade, contexto, saída, structured
  output, política de dados, disponibilidade, custo, limites reais quando
  conhecidos). Quotas compartilhadas entre diálogo, visão, web e reflexão;
  scheduler respeita Retry-After, evita starvation e picos de requisições.
- Liveness: separar tempo de conexão/headers, primeiro evento, primeiro
  conteúdo, idle entre eventos e progresso semântico, limite de segurança por
  tentativa. Stream saudável não cancelado apenas por budget HTTP; heartbeat
  sem progresso não é resposta. Safety cap e idle configuráveis medidos;
  não remover todos os timeouts. Cancelar/join antes de nova tentativa.
- HTTP 413/context overflow: distinguir body/context/TPM se resposta e
  documentação permitirem; projeção compacta corretiva com original e gates
  preservados, reserva saída, roteamento compatível se irredutível. Não repetir
  envelope rejeitado, não envenenar saúde global com erro específico de input.
  RUN 03 refina o compilador.
- 429/5xx/desconexão/timeout: backoff exponencial + jitter, Retry-After, circuit
  breaker com half-open e cooldown; tentativa independente por candidato.
  Todos temporariamente indisponíveis -> retry_wait com causa e próxima tentativa.
  401/config/permissão inválida -> blocked, retomar após correção; sem loops.
  Erros de schema/JSON: reparo limitado e validado; nunca liberar parcial bruto.
- Roteamento por capacidade/saúde/latência observada e orçamento. Preferir um
  modelo por vez em conversa comum; hedging somente com evidência de benefício,
  consentimento, quota e dedupe. Não fazer probes que gastem quota sem necessidade.
- Diagnósticos por provider/attempt/turn, status e progresso content-free.

Gate: simular stream saudável após 87 s, stream sem progresso, 413 seguido
de projeção menor, 429 Retry-After, 503 e desconexão; indisponibilidade de todos
por 2–3 min simulados e posterior retorno; uma resposta sem reenvio manual.
Teste cancelamento, quota compartilhada, ausência de tempestade de retries,
reserva de fallback, identidade/memória e respeito ao custo. Relatar casos
permanentes como bloqueados, não sucesso.

## 6. RUN 03 — Adaptive Context & Comprehension Engine

Dependências: 01–02. Reusar Context Compiler, ACIR, DialoguePlan, memory retrieval,
prompt_frame e profile. Fazer inventário de tokens por bloco, eliminar duplicação
de protocolos e contexto repetido, comparar comportamento com baseline.

Entregáveis:
- Orçamento por provedor e tarefa: contexto + reserva de saída e overhead
  de mensagens/estrutura, limite de bytes e quota quando conhecidos. Estimativa
  não é contagem real; usar tokenizer do modelo quando disponível/viável e
  telemetria de tokens reportados. Perfil mínimo fiel, sem cortar personalidade
  ou guardrails; reduzir redundância e projeções opcionais primeiro.
- Hierarquia: fala atual e regras/identidade essenciais, contexto causal
  imediato, memória relevante recuperável sob demanda, evidências necessárias,
  opcionais. Não enviar a vida inteira da Amora para cada saudação. Reusar
  cache/prefixo apenas se suportado pelo provider, escopo e privacidade corretos.
- AMD: original íntegro e offset spans sem reescrita. Deterministic boundaries
  por parágrafo/sentença, fallback por token/limite seguro; planner leve só
  quando necessária desambiguação. Minimizar número de blocos. Mensagem normal
  ou que cabe com contexto compactado não divide. Se houver subtarefas
  independentes, decompor opcionalmente. Se não cabe, primeira passagem
  por blocos com overlap/referências quando necessário, consolidar mapa global
  antes de emitir qualquer resposta.
- Mapa global registra sujeitos, relações, referências a imagem, decisões,
  correções, negações, dependências entre trechos e perguntas pendentes.
  Resumo/mapa não substitui original; leitura sob demanda de trechos relevantes.
  Tratar casos de pronome/negação na última parte e contradições intencionais.
- Geração por partes somente quando necessário; todas as partes conhecem
  mapa global, estado do que já foi respondido e trechos necessários.
  Integrar, evitar duplicação e validar coerência antes da entrega. RUN 06
  cuidará da emissão progressiva, não confundir com divisão de entrada.
- Exemplo obrigatório: usuário elogia carro A, depois afirma que NÃO vai
  comprar A, escolhe B e pede avaliação da imagem de B. Não recomendar compra
  de A como se primeira frase fosse decisão. Pergunta final sobre imagem
  deve alcançar especialista na RUN 04.
- Conteúdo longo não resumido permanentemente. Se nem decomposição segura
  atender limite de recursos, registrar pendência/explicar precisamente.

Gate: corpus de mensagens longas reais sintéticas, múltiplos assuntos,
negações tardias, dependências, imagem referenciada ao final, memória e
mudanças de opinião; prova de cobertura dos trechos sem alteração do original,
comparar tokens, chamadas, latência e qualidade semântica. Meta 30–50% de
redução em turnos com redundância (não promessa geral), conversa curta não
deve ficar mais lenta por planner extra. 413 recovery integrado à RUN 02.

## 7. RUN 04 — Advanced Multimodal Perception

Dependências 01–03. Reusar contratos de attachments, adapter Discord, visão
existente, ACIR e visual_context. Preservar validação de MIME, bytes, tamanho,
origem, consentimento e isolamento; não persistir raw bytes nem URL assinada
sem política aprovada e mecanismo seguro. Reidratação de anexo pode expirar:
registrar indisponibilidade real e pedir reenvio se necessário, preservar
texto e observações permitidas.

Entregáveis:
- Percepção rápida (descrição de foto), direcionada (pergunta relevante em
  qualquer posição do texto), detalhada (OCR, regiões, screenshot técnico,
  documento/imagem). Orçamento por tipo e capacidade, não limites arbitrários
  únicos; eliminar corte cego [:500] no foco visual.
- Mapear imagem/anexo ao turno e partes corretas. Suportar múltiplas imagens
  com IDs/ordem, pergunta e diferenças; não associar foto errada. Se texto
  e imagem juntos, responder ambos de forma integrada e não como dois usuários.
- Observações estruturadas com confiança, incerteza, fonte, versão e validade.
  Cache/checkpoint de observação autorizada por mensagem/anexo, sem inferir
  identidade/emoção como fato nem reaproveitar observação de foto diferente.
- Retry/fallback de visão pelo orquestrador, sem enviar imagem a provider
  adicional sem autorização. Falhas de download, transformação, formato,
  quota, recusa, timeout e OCR têm causas distintas. Não dizer que viu preto
  se não recebeu pixels nem inventar detalhes. Permitir progresso textual
  independente se pergunta não depende da imagem; se depende, aguardar ou
  marcar resposta parcial explicitamente.
- Paralelismo de especialistas independentes com limites; se pesquisa depende
  de identificação visual, visão precede web. Sem payload base64 em LLM textual,
  logs, backups de diagnóstico ou checkpoints comuns.

Gate: texto longo com pergunta visual no final, várias imagens e referência
cruzada, screenshot legível/ilegível, formato não suportado, 429, falha e
retomada sem repetir visão bem-sucedida, anexo expirado, edição/exclusão,
revogação e isolamento. Medir p50/p95, tokens, quota e precisão semântica
comparada a exemplos de referência. Certificar no Discord real.

## 8. RUN 05 — Autonomous Web Intelligence

Dependências 01–04 (interface de especialistas e evidência). Reusar WebNavigator,
RemoteReader, workspace, query planner e web_delivery. Web rápida é default;
aprofundada por intenção/complexidade. Não instalar browser local permanente.
Pesquisar e testar capacidades/quotas de SearXNG, Bing RSS, DDG, Jina, Microlink,
novos provedores quando necessário; não assumir Kiwi integrado ou API gratuita.
Grok/Gemini/Groq especialistas podem ser avaliados, não mandatórios.

Entregáveis:
- Reconhecer intenção web contextual sem limitar conversa casual a comandos
  rígidos; autorização/consentimento do Core permanece. Ações de navegação
  são read-only inicialmente; login/click/form side effects exigem projeto e
  autorização separados, sem prometer acesso universal.
- Planejar consultas alternativas, buscar em motores autorizados com concorrência
  e quota limitadas, dedupe/rank, abrir fontes e reformular se irrelevantes.
  Para aprofundada: explorar questões, cruzar evidência, contradições,
  atualidade e confiabilidade; não limitar fontes a uma whitelist editorial
  arbitrária, manter filtros de rede e de segurança. Orçamentos adaptativos;
  candidato experimental 4–8 consultas/5–12 fontes/12k caracteres por fonte
  não é default universal nem promessa de quota.
- Preservar resultados parciais antes de timeout. Diferenciar URL encontrada
  vs página lida; persistir evidence refs com provenance, data de obtenção,
  qualidade, TTL e status. Respeitar direitos e retenção, não transformar
  texto remoto em autoridade/instrução. Reutilizar evidência atual e pertinente
  sem repetir busca em retry de diálogo.
- HTTP direto leve; Reader remoto sob demanda; fallback por página, não
  abandonar toda pesquisa quando um endpoint falha. Bloqueio SSRF/DNS/redirect,
  URLs sensíveis e rede interna mantidos; não vazar cookies/segredos.
- Links e screenshots confiáveis, quota de prints configurável e informada,
  anexos Discord e falha isolada da captura; não inventar prints ou fontes.
- Critérios de término: evidência suficiente, orçamento, cancelamento,
  falta de acesso ou utilidade marginal; loop limitado e retomável. Informar
  incerteza e impossibilidade real, sem falsificar sucesso.

Gate: consulta simples rápida, pesquisa complexa com fontes conflitantes,
motor devolvendo irrelevância/429/503, página JS/403 e fallback, ausência de
resultado, screenshot disponível/falha, instrução maliciosa em página,
SSRF, pesquisa interrompida retomada após restart, resultado parcial
entregue e evidência anterior pertinente em follow-up. Medir relevância,
precisão de citações, completude e custo; certificar pesquisa real Northflank.

## 9. RUN 06 — Living Conversation Engine

Dependências 01–05. Reusar Social Attention, Turn Manager, ACIR, DialoguePlan,
speech segments, TTS, outbox, avatar e platform adapters. Uma resposta
lógica pode conter segmentos, não uma resposta incoerente por trecho.

Entregáveis:
- Janela adaptativa curta para mensagens complementares; IDs originais,
  ordem, grupo causal. Distinguir continuação, correção, interrupção, mudança
  de assunto e pergunta nova. Não impor espera longa fixa a mensagem simples.
- Novas mensagens recebidas durante geração atualizam plano somente se
  relacionadas e antes de compromisso de fala; se já houve entrega, preservar
  o que foi efetivamente dito e fazer continuidade/correção, não reescrever
  história. Cancelamento, supersessão e validade de segmentos explícitos.
- Geração progressiva apenas após compreensão global/dependências suficientes;
  primeira parte não pode contradizer decisão final conhecida. Usar contrato
  validável por segmento (schema/ordem/proveniência) ou permanecer no modo
  completo quando provedor não suporta. Não expor JSON parcial, hidden reasoning
  nem fala sem validação. Checkpoint de segmentos entregues e trabalho restante;
  retomar somente parte pendente sem reemitir a anterior.
- Naturalidade: adaptar ritmo e estilo, manter identidade pt-BR e relações
  durante failover. TTS pode iniciar por segmento válido quando seguro;
  respeitar barge-in, destino WEB_SAFARI vs DEVICE_A15, identidade vocal
  congelada e ACK de áudio. Avatar reage à semântica/estado observado, não
  inventa ações. Sem mudar rigs ou voz aprovada sem autorização.
- Aviso de pendência técnico não vira conversa social. Mostrar estados úteis
  sem spam. A resposta pode demorar e chegar automaticamente, preservando
  mensagens posteriores e causalidade.

Gate: várias mensagens consecutivas, mudança de assunto, correção,
interrupção antes/depois do primeiro segmento, falha no segundo segmento,
reinício e recuperação, edição/exclusão e ausência de duplicação; conversa
curta mantém rapidez; texto/voz/avatar e memória não regridem. Certificar
Discord e superfícies afetadas; medir TTFS (primeiro segmento validado e
entregue), latência total e qualidade de diálogo.

## 10. RUN 07 — Cognitive Integration & Certification

Dependências: gates 01–06. Auditar ponta a ponta; remover caminhos legados
substituídos somente após equivalência, migração e rollback comprovados.
Não deixar dois routers/queues/retry engines competindo ou flags sem dono.
Atualizar canon, ADRs, roadmap, PROJECT_STATE, docs e runbook operacional.

Entregáveis:
- Diagnóstico único por turn_id/job_id/stage/attempt/provider, arquivo/função
  ou módulo, fase, causa, cadeia de eventos correlacionada, status HTTP seguro,
  retryability, próximo passo e timestamps. Stack traces somente sanitizados
  em acesso restrito. Sem texto do usuário, prompts, tokens, imagens ou
  URLs com segredo. Separar falha de modelo, cancelamento do caller, entrega,
  storage e política. Não marcar fallback técnico como sucesso semântico.
- Métricas: mensagens aceitas/respondidas/pendentes/bloqueadas/canceladas,
  idade da fila, recovered/failed, duplicatas, TTFS, p50/p95 de turno e etapas,
  tokens por componente, chamadas por tarefa, rate limit, CPU/RAM/lag do loop,
  operações DB, custo. Definir denominadores e baseline reproduzível.
- Testes de carga e duração (incluindo falhas simultâneas, provider offline,
  reinício, perda de rede, anexos, pesquisa, mensagens longas/consecutivas,
  memória, voz e avatar), regressões e segurança. Testar em orçamento real,
  sem instalar serviço pesado nem aumentar plano por padrão.
- Certificação Northflank com configuração/commit e backend confirmados,
  único Gateway, rollback testado, logs sanitizados e resultado por cenário.
  Medir antes/depois com mesma carga, não concluir por teste unitário isolado.

Gate final: suite integrada verde + laboratório PostgreSQL + validação real
documentada de cenários críticos, recuperação sem perda/deduplicação, latência
e recursos dentro de limites acordados. Se live não puder ser feito, status
LIVE_PENDING/BLOCKED com procedimento e evidência faltante; não certificar por
suposição. Metas: 0 perdas/duplicatas nos testes, recuperação automática de
transientes, nenhuma truncagem silenciosa, 30–50% menos tokens em envelopes
redundantes, menos chamadas desnecessárias, identidade/memória preservadas,
512 MB/0,2 vCPU como alvo inicial. Metas não são resultados já alcançados.

## 11. Regras obrigatórias para TODAS as sete RUNs

- Escopo e ciclo completos; não encerrar pela existência de arquivo, teste
  unitário isolado, mock ou PR. Não espremer RUNs para fazer tudo meia boca.
  Se um gate falhar, corrigir na RUN atual; não empurrar problema invisível.
- Antes: ler canon/ADR/diário/HEAD, levantar baseline, ownership, dependências,
  cenários de teste e riscos; registrar decisão e status. Pesquisa externa
  quando tecnologia/quota/licença atual depender disso, fontes primárias.
- Durante: branch e PR revisáveis, contratos tipados, migrations para estado
  persistente, compatibilidade de adapters, segurança/privacidade, feature
  rollout e rollback. Reusar e consolidar; sem gambiarras, sem mudanças fora
  do escopo, sem desligar host funcional, sem deploy concorrente Discord.
- Depois: integração real, testes unitários, contrato, integração, falhas
  sintéticas, restart/dedupe, regressão e comparação de desempenho conforme
  impacto; CI obrigatório; registrar SHA, links, comandos, totais e resultados
  reais. Não inventar PASS nem confundir envio de erro com resposta.
- Documentar código alterado, erros encontrados, causa/evidência, correções,
  alternativas rejeitadas, impacto, riscos residuais, rollback, próximo passo
  e o que não foi feito. Comentar issue/PR e atualizar RUN_LOG.md na própria
  revisão. Resultados LIVE_PENDING podem coexistir com CI_PASS, nunca com
  certificação final falsa.
- Atualizar canon/roadmap/ADRs/PROJECT_STATE quando comportamento efetivo
  mudar. Preservar histórico, não reescrever incidentes. Guardrails em AGENTS.md.
- Nenhum avanço automático para a próxima RUN baseado em tempo. Gate explícito,
  evidência e decisão do proprietário para exceção; não prometer trabalho em
  background. Preferir rollback seguro a introduzir segunda implementação.
- Sem remover timeouts indiscriminadamente, dividir toda entrada, responder
  partes cegamente, sumarizar original permanentemente, chamar múltiplos
  modelos em cada turno, instalar frameworks por moda ou expandir contexto
  sem medição. Não autoeditar produção.
- Se mudança em documento contradiz canon/ADR, explicitar e revisar antes de
  código. Novas ideias vão ao backlog com RUN proprietária e critérios;
  não desaparecer nem inflar escopo sem controle.

## 12. Referências para implementação (padrões, não dependências obrigatórias)

Consultar documentação upstream e versão atual antes de adotar. As ideias
abaixo inspiram o design; não afirmam que outro projeto já resolveu a
experiência específica da Amora.

- MemGPT/Letta: memória hierárquica/contexto virtual, https://arxiv.org/abs/2310.08560
  e https://docs.letta.com/ (RUN 03).
- LangGraph persistence/checkpoint/durable execution:
  https://docs.langchain.com/oss/python/langgraph/persistence (RUN 01).
- Temporal durable execution/retries: https://docs.temporal.io/ (RUN 01/02).
- LiteLLM router/reliability: https://docs.litellm.ai/docs/routing (RUN 02).
- LiveKit Agents turn handling: https://docs.livekit.io/agents/ (RUN 06).
- SillyTavern documentação de contexto/personagem: https://docs.sillytavern.app/
  (RUN 03; não copiar prompt/persona nem substituir ACIR).
- Generative Agents: https://arxiv.org/abs/2304.03442 (memória, reflexão,
  planejamento; preservar authority e gates locais).
- Anthropic Building Effective Agents: https://www.anthropic.com/research/building-effective-agents
  (decomposição, workflows, paralelismo e critério de parada).
- Lost in the Middle: https://arxiv.org/abs/2307.03172 (contexto longo não
  garante atenção uniforme; medir antes de aumentar tokens).
- Groq rate limits: https://console.groq.com/docs/rate-limits (verificar conta
  efetiva; contexto máximo != TPM != limite de body; não hardcodar tabela antiga).

## 13. Decisões ainda a fechar no início da RUN correspondente

Não deixar lacunas de implementação disfarçadas de requisitos prontos:
- RUN 01: schema/índices e retenção exatos, contrato ACK compatível, lease/renovação,
  limites da fila, política de backpressure, estratégia de rollback e
  reidratação de jobs; confirmar backend/live. ADR antes de migration.
- RUN 02: limites reais/headers por provedor, safety/idle/backoff e quotas
  medidos, classes de erro e política de custo; testes com relógio controlado.
- RUN 03: orçamento medido por bloco, tokenizer disponível, algoritmo de
  offsets/overlap e fallback para texto irreduzível; dataset de avaliação.
- RUN 04: retenção/TTL e recuperação de anexo autorizado, capacidades de
  múltiplas imagens e OCR, política de fallback visual.
- RUN 05: motores reais/termos/quotas, orçamento e critério de evidência,
  persistência/TTL e screenshot; sem prometer acesso universal.
- RUN 06: contrato incremental/segmento, agrupamento/interrupção, dedupe por
  segmento e coordenação com voz e avatar; sem alterar voz congelada.
- RUN 07: carga representativa, metodologia e thresholds de certificação
  acordados por baseline, rollback e janela live.

Essas decisões são trabalho explícito de cada RUN, não autorização para
inventar valores ou mudar escopo. O diário registra resolução, referência e
evidência. **Próximo trabalho: RUN 02, a partir do HEAD CI_PASS da RUN 01.**
