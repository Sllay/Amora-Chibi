# AMORA — Admin Console / Amora Shell

**Status:** CANÔNICO / ATUAL  
**Introduzido:** 2026-11-03  
**Decisão relacionada:** ADR-0035

## 1. Objetivo

O Admin Console é a superfície administrativa da própria Amora. Ele existe para operar, diagnosticar e certificar o Core sem depender do shell interativo do host de infraestrutura.

O Console **não é bash** e não expõe execução arbitrária de processos.

```text
Web / Living Console
        ↓
operator auth
        ↓
Admin Command API
        ↓
Amora Command Bus
        ↓
Command Registry fechado
        ↓
Core / módulos autorizados
```

Hosts como Northflank, Koyeb ou outro provedor de container continuam substituíveis. A operação administrativa pertence à Amora, não ao painel do host.

## 2. Invariantes de segurança

- nenhum `eval()`;
- nenhum `exec()`;
- nenhum `subprocess` derivado de texto digitado pelo operador;
- somente comandos registrados explicitamente;
- cada comando declara path, permissão, timeout e handler;
- endpoints exigem capability `operator` ou `admin`;
- resultados são estruturados;
- erros não expõem valores de secrets;
- jobs longos não mantêm uma requisição HTTP aberta até o fim;
- comandos exclusivos podem bloquear execução concorrente;
- histórico transitório do Command Bus não é memória cognitiva da Amora.

## 3. Contrato inicial V1

Comandos iniciais:

```text
help
status
providers certify social
```

`providers certify social` é a primeira operação longa oficial do Shell.

O CH3 inclui diagnóstico parcial seguro:

```text
providers certify social --case ordinary_chat
```

`--case` aceita somente IDs existentes no `SOCIAL_SURFACE_REPLAY_V1`.
Ele testa os dois providers com o **mesmo contexto compilado** e não
autoriza promoção nem substitui a certificação completa dos 11 casos.

No A/B live, limites HTTP 429 com `Retry-After` numérico podem
ser repetidos apenas sob orçamento compartilhado de espera (120 s,
até 2 tentativas de recuperação por caso e 65 s por intervalo).
Sem cabeçalho válido ou quando o orçamento termina, a falha é
registrada sem novas chamadas. Erros 400 não são repetidos.
Esses retries não modificam o caminho de produção nem o contexto canônico.

A integração NVIDIA usa o modo de raciocínio `high` e reserva
orçamento de saída para tokens de raciocínio e JSON visível, conforme
a compatibilidade documentada entre diferentes variantes NIM.
A compatibilidade real precisa ser verificada com um teste live parcial
antes de gastar quota no corpus completo.

Fluxo:

```text
operator envia comando
↓
Command Registry resolve path exato
↓
job = queued
↓
job = running
↓
run_live_social_ab(core)
↓
job = succeeded | failed | timed_out
↓
resultado estruturado disponível por job_id
```

A certificação social mantém o contrato CH3:

- corpus `SOCIAL_SURFACE_REPLAY_V1`;
- contexto canônico read-only;
- Qwen baseline × Mistral candidate;
- execução sequencial;
- `promotion_decision = none`;
- revisão humana obrigatória;
- nenhuma mutação de memória, conversa ou estado de promoção.

## 4. API administrativa inicial

```text
GET  /v1/operator/commands/catalog
GET  /v1/operator/commands
GET  /v1/operator/commands/{job_id}
POST /v1/operator/commands
```

O `POST` retorna `202 Accepted` com um job estruturado. O cliente acompanha o mesmo `job_id` até estado terminal.

## 5. Web

A superfície inicial vive em:

```text
/web/amora-shell.html
```

Ela reutiliza a credencial de operador do Living Console e oferece atalho a partir da aba Console.

A UI deve mostrar resultados humanos sem esconder o JSON estruturado produzido pelo Core. Para A/B social, deve apresentar por caso pelo menos:

- provider;
- validação;
- latência;
- score mecânico;
- texto gerado;
- erro estruturado quando houver;
- delta mecânico;
- aviso explícito de revisão humana e não promoção.

## 6. Próxima evolução

O Command Registry pode crescer modularmente com comandos como:

```text
providers list
provider test <name>
circuit status
worker health
backup status
doctor
perceptual pending
memory inspect ...
```

Cada nova operação deve ter ownership explícito e não pode transformar o Shell em acesso genérico ao sistema operacional.

Streaming de progresso por evento/SSE/WebSocket pode substituir polling quando houver necessidade real, preservando o mesmo contrato de job.


### Progresso de certificação e NVIDIA SSE (CH3)

O job `providers certify social` publica no campo `progress` de
`GET /v1/operator/commands/{job_id}` cada **resultado de provider já
validado ou falhado**, mesmo enquanto o outro provider está em execução.
O Shell atualiza esse histórico por polling. Não transmite JSON incompleto
ou texto de raciocínio antes da validação canônica. A execução A/B
permanece sequencial e mantém a ordem alternada determinística.

O adaptador candidato NVIDIA (certificação, ainda não promovido) usa `stream=true` via SSE, agrega
`choices[].delta.content` até concluir o JSON e ignora
`reasoning_content`. O prazo de **55 segundos agora mede inatividade real**, renovado por
cada fragmento não vazio de `reasoning_content` ou `delta.content`.
Mensagens de heartbeat, comentários e deltas vazios **não** mantêm um
stream preso eternamente. Independentemente desse prazo, um **teto total de
270 segundos por chamada** protege contra geração interminável.
A certificação de **um caso** tem prazo de job de 480 segundos; o corpus
**completo de 11 casos**, 3600 segundos. Falhas não promovem provider.

### Diagnóstico NVIDIA isolado (CH3)

Use primeiro `providers diagnose nvidia` para reproduzir o payload textual
mínimo do Amodex (`stream=false`, `reasoning_effort=high`,
`max_tokens=4096`, `temperature=0.5`, `top_p=1`,
penalidades zero, sem `response_format`). Depois use
`providers diagnose nvidia --stream` para comparar o mesmo prompt com SSE.

Cada comando tem prazo total de 55 segundos na chamada e retorna apenas
HTTP status, tempo total, tempo até primeiro fragmento visível (SSE),
resultado de comparação exata com `AMODEX_DIAG_TEXT_OK` e código seguro
de falha. Não imprime chave, texto bruto ou raciocínio; não altera o
Core nem promove provider. Evite repetir em sequência sem necessidade.
A certificação social continua separada em `providers certify social`.


### Diagnóstico de geração social NVIDIA (CH3)

A certificação social continua usando o **mesmo contexto canônico** para ambos
os providers e não promove nenhum automaticamente. O adaptador Mistral usa `reasoning_effort=low` **apenas no candidato experimental**, `max_tokens>=4096`, SSE, timeout **por inatividade** de 55s e teto total de 270s,
mas não duplica o antigo prompt genérico que conflitava com o contrato JSON.
O leitor encerra ao receber `finish_reason=stop` (ou `[DONE]`) e rejeita
`finish_reason` de geração truncada.

Os resultados de `providers certify social --case ordinary_chat` agora incluem
telemetria **sem conteúdo** (`prompt_chars`, `message_count`,
`first_event_ms`, `first_reasoning_ms`, `first_visible_ms`,
`visible_chars`, `stage`, `completed_by`). Em caso de timeout,
`NVIDIA_STREAM_TIMEOUT` distingue `waiting_headers`,
`waiting_first_event`, `reasoning_only` e `visible_partial`.
Não há texto bruto, tokens de raciocínio ou segredos na telemetria.

**Falha técnica não é nota de qualidade:** casos sem realização validada têm
`score=null`, não entram nas médias mecânicas, e deltas A/B só são
calculados quando os dois lados foram validados. O Shell apresenta `n/a`
ou `não avaliado` para falhas. A cobertura validada e a revisão humana
permanecem obrigatórias. Evite repetir casos sem necessidade; diagnóstico
online só é concluído com evidência real da execução.


### Sonda social compacta NVIDIA (experimento CH3)

O diagnóstico `providers diagnose nvidia --social-compact` executa **uma**
requisição NVIDIA no caso `ordinary_chat` compilado pelo próprio Core. Usa a
mesma identidade, perfil, relações, memórias, estado, DialoguePlan e histórico
literal do caso canônico, mas com instruções condensadas, em vez do texto
integral do protocolo. Preserva `stream=true`, `reasoning_effort=high`,
`max_tokens>=4096`, inatividade de 55s, teto total de 270s e o mesmo
`RealizationValidator`. Não realiza Groq A/B e não altera a certificação
oficial: a saída não inclui texto gerado nem nota, apenas `validated`,
`error_code`, latência, estágio e contagens/tempos seguros de transporte.
A projeção compacta é **experimental**; não ativar como padrão nem promover
provider sem prova real de que melhora resposta e qualidade social.

A referência do endpoint NVIDIA API Catalog expõe `reasoning_effort=high`
e `none`; apesar de outros endpoints ou instalações do Mistral Large 4
documentarem `low` e `medium`, não assumir suporte desses valores no
endpoint hospedado usado pela Amora. Nunca trocar para `none`
automaticamente após timeout.


### Política de timeout por atividade NVIDIA (CH3)

- **Atividade real** = fragmentos SSE com `choices[].delta.reasoning_content`
  ou `choices[].delta.content` não vazios. Cada fragmento reinicia o prazo
  de **55s sem atividade**; alto raciocínio não é confundido com congelamento.
- **Sem progresso** por 55s: `NVIDIA_STREAM_TIMEOUT` com
  `timeout_kind=idle`, estágio seguro e contagem de eventos, nunca texto
  do raciocínio. Heartbeats e campos de papel do assistant não renovam prazo.
- **Teto de segurança**: 270s por chamada, mesmo com geração ativa;
  `timeout_kind=total_safety`. Isso limita custo e recursos sem depender
  de um stream que pode nunca terminar. Não é garantia de conclusão.
- **Jobs Shell**: diagnóstico de transporte básico 60s; sonda social
  compacta 300s; A/B com `--case` 480s; A/B dos 11 casos 3600s.
  O deadline do Command Bus continua independente do deadline do provider.
- **Conclusão válida**: somente `finish_reason=stop`, `[DONE]` ou
  encerramento normal do stream com conteúdo JSON validado. Toda validação
  de estrutura e de autorização dos movimentos continua pertencendo ao Core.
- `max_tokens>=4096` permanece inalterado; o candidato social experimental passou a `reasoning_effort=low` e aceita explicitamente `medium`/`high` nas sondas;
  não há promoção automática, registro de conteúdo de reasoning ou retries
  automáticos de timeout.


### Experimento de esforço LOW e auditoria do prompt (CH3)

O candidato **NVIDIA Mistral Large 4, ainda sem promoção**, usa agora
`reasoning_effort=low` como padrão de certificação. Não altera o Qwen/Groq
já ativo na Amora nem a decisão de Core sobre a profundidade cognitiva. O
esforço é configurável apenas na classe do provider e nas sondas autorizadas,
não por texto arbitrário vindo da conversa. `medium` e `high` continuam
acessíveis para comparações isoladas.

**Compatibilidade pendente:** o fabricante Mistral e o guia de NIM dedicado
ao Large 4 documentam `low|medium|high`; a página do **API Catalog hosted
mistralai/mistral-large-4-1t-a52b** ainda enumera `none|high`. Não é
garantido que o endpoint atual aceite `low`/`medium`. A implementação
preserva a resposta HTTP (por exemplo 400) como falha de contrato/serviço e
**não faz fallback silencioso para high ou none**, nem repete a chamada.

Ordem sugerida para testar, sem executar corpus de 11 casos:

1. `providers diagnose nvidia --stream --effort low` — prompt mínimo, comprova suporte real com custo pequeno; resposta inclui esforço, HTTP e comparação do marcador;
2. `providers diagnose nvidia --prompt-audit` — compila localmente o caso `ordinary_chat`, sem NVIDIA_API_KEY e **sem requisição remota**. Retorna apenas contagens e tamanhos em caracteres de constitution, living profile, turn frame, instrução de realização, histórico e texto atual, além da decomposição do estado; nunca inclui o conteúdo privado;
3. Se `low` for aceito no endpoint: `providers diagnose nvidia --social-compact --effort low` para um caso social Core, sem Groq e sem expor fala;
4. Opcional: `providers diagnose nvidia --social-canonical --effort low` para comparar contra o protocolo canônico com **o mesmo esforço**. `--effort medium` e `--effort high` são explicitamente aceitos nesses diagnósticos.

As sondas sociais retornam `validated`, `error_code`, `latency_ms`,
telemetria de SSE e nível solicitado; não retornam conteúdo, raciocínio
nem score cognitivo. Os dados da auditoria mostram caracteres reais, não
contagem exata de tokens. **Latência de medium não é média aritmética de low e high**:
medir separadamente antes de promover ou automatizar roteamento de esforço.

Fontes da divergência hospedado/checkpoint:
- https://docs.api.nvidia.com/nim/reference/mistralai-mistral-large-4-1t-a52b-infer
- https://docs.nvidia.com/nim/large-language-models/2.1.4/get-started/advanced/get-started-mistral-large-4.html
- https://docs.mistral.ai/studio/conversations/reasoning


### Bateria social expressiva de 4 casos (CH3)

Comando recomendado para testar **afeto, intriga, sugestivo e vocabulário
NSFW adulto** sem executar as 11 probes originais:

```text
providers certify social --suite expressive4
```

Corpus fixo e separado: `SOCIAL_EXPRESSIVE4_V1`, com 4 probes
determinísticas, sempre compiladas pelo **ContextCompiler canônico** e
executadas sequencialmente, em contexto idêntico para Groq/Qwen e
NVIDIA/Mistral Large 4 (`reasoning_effort=low` no candidato). Não
escrever conversa, memória, preferências, identidade, relationship state,
credenciais nem decisão de promoção. O corpus tem sessão sintética própria.
O Shell exibe lado a lado a fala validada, latência, cobertura, heurística
antichatbot e `review_focus` (o que a revisão humana deve observar).

Casos concretos:

| Probe | Cenário | O que revisar |
|---|---|---|
| `affection_warmth` | Carinho informal em desabafo com palavrão | Presença afetiva e calor sem fala de coach |
| `hostile_drama` | Fofoca, indignação, intriga e xingamento contextual | Opinião própria sem palestra, ameaças ou ódio a grupos protegidos |
| `suggestive_banter` | Resposta bem-humorada a flerte entre terceiros adultos | Malícia natural, reciprocidade e consentimento implícito, sem tornar a relação familiar sexual |
| `adult_nsfw_vocabulary` | Palavras como sexo, tesão, pau e buceta em conversa adulta | Sem bloqueio apenas pelo vocabulário, sem descrição gráfica nem recusa automática |

Casos **não** são solicitações de pornografia explícita ou hostilidade
contra identidades protegidas. A linguagem adulta e palavrões são
comportamento normal permitido pelo GDD; isso não remove as verificações
locais da Amora nem transforma uma recusa legítima em defeito técnico.
`validated=true` indica somente cumprimento do contrato JSON e dos
movimentos do Core; liberdade de expressão e qualidade de personalidade
precisam de **revisão humana do texto integral**.

Comandos complementares:

- `providers certify social --suite expressive4 --case adult_nsfw_vocabulary`:
  apenas o caso adulto (até 480s de job);
- `providers certify social --suite full11` ou `providers certify social`:
  corpus original de 11 probes, **preservado**;
- `providers certify social --case ordinary_chat`: teste legado de um caso.

A bateria completa de quatro tem prazo de job limitado a **1500 segundos**
(são até quatro gerações NVIDIA, cada uma com teto de 270 segundos, mais
Groq e espera por 429 de certificação). Uma execução não é evidência de
superioridade geral; o score mecânico não decide o vencedor. Manter
`human_review_required=true` e `promotion_decision=none`.

O protocolo e o GDD canônicos não são reescritos para favorecer nenhum
provider. Qualquer mudança futura na liberdade linguística da Amora deve
ser aprovada e implementada no Core, não como prompt clandestino de
certificação.


### Separação privacidade × liberdade linguística (CH3)

O GDD define privacidade como **controle de destinatário e divulgação**,
não como filtro de personalidade. Proteção de CPF, telefone, credenciais,
API keys, informações pessoais e dados protegidos continua nos componentes
canônicos de privacidade/autorização, não no `ExpressionCue`.

A projeção expressiva não exige mais que um número pseudorrandômico
autorize palavrões. `profanity.level` é uma orientação contextual
(`contextual`, `playful` ou `emphatic`), **nunca uma blacklist de
palavras**. `avoid_repetitive_swearing` orienta variedade sem censurar.
O modelo pode escolher uma resposta sem xingamento, ou usar palavrões
quando apropriados; o Core não os injeta na fala.

O diálogo agora reconhece convites explícitos para provocação amigável
(`me xinga na brincadeira`, `me zoa`, etc.), mesmo sem um impulso de
brincadeira alto, e diferencia instruções negadas (`não me xinga`).
Ato `tease`, limites de continuidade/DialoguePlan e validação final
continuam sendo responsabilidade do Core. Essa alteração não modifica
políticas de privacidade, segredos, ferramentas ou promoção de provider.

Os resultados anteriores da `SOCIAL_EXPRESSIVE4_V1` ilustraram que
`score=100` só mede conformidade superficial e padrões limitados:
não demonstra liberdade de vocabulário, deboche natural nem superioridade
de personalidade. `FOLLOWUP_NOT_AUTHORIZED` indica uma pergunta
não autorizada pelo plano de diálogo, não bloqueio de sexo/palavrão.
