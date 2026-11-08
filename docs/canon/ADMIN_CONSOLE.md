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


### Bateria Nuance4 — profundidade e continuidade (CH3)

```text
providers certify social --suite nuance4
```

A Expressive4 mede a naturalidade de linguagem em situações **isoladas**;
a `SOCIAL_NUANCE4_V1` mede a capacidade de interpretar a mesma cena com
um **histórico fictício de 2–3 mensagens anteriores**. Os quatro casos
têm contexto controlado, igual para Groq/Qwen e NVIDIA/Mistral:
`layered_disclosure`, `contextual_ambiguity`, `rumor_and_reaction`
e `adult_relationship_subtext`. As mensagens de teste não são escritas
no banco real, não viram memória e não autorizam fatos canônicos.
O compilador constrói o Core Context normalmente; o replay acrescenta
apenas o histórico ficcional, recompila o DialoguePlan pelo mesmo
`DialogueOrchestrator` do Core e aplica seu budget existente antes de
comparar os providers.

**Hipótese sob teste:** perguntas profundas não exigem sempre textões,
mas a resposta precisa usar o detalhe relevante da história e não apenas
dizer que o assunto é normal ou que entende o usuário. Um desabafo
complexo não deve ser obrigado a caber em `depth=short`; a mudança de
Core permite `medium` nesses casos e mantém desabafos triviais como
`short`. `RealizationValidator` e os limites do plano continuam ativos.

O score mecânico atual **não mede qualidade interpretativa**. Revisão
humana deve distinguir: usar a conversa anterior, reconhecer a tensão,
evitar inventar fatos, manter opinião própria, não confundir sujeitos,
não sexualizar toda ambiguidade, nem responder com roteiro genérico.
Mais palavras não equivalem a mais profundidade.

O Shell mostra `review_focus` por caso. Diagnóstico de somente um caso:

```text
providers certify social --suite nuance4 --case layered_disclosure
```

Bateria completa de 4 casos: job de até 1500s. O corpus original de 11 e
`--suite expressive4` continuam intactos. Nenhuma aprovação ou promoção
automática é permitida.


### Diagnóstico do fechamento rejeitado em certificação A/B

Quando o provider produz um JSON válido, mas o `RealizationValidator`
rejeita `FOLLOWUP_NOT_AUTHORIZED`, o relatório agora inclui
`validation_failure_detail` e o Shell exibe `followup_rejection=` com
uma das categorias fixas:

- `closing_question`: campo `closing` do JSON marcou fechamento de pergunta;
- `terminal_question_mark`: fala terminava literalmente com `?`;
- `followup_move`: o JSON propôs movimento de continuação não autorizado;
- `unknown`: causa não discriminada pelo diagnóstico não sensível.

Esse diagnóstico **não revela** texto recusado, histórico, raciocínio ou
segredos. Não flexibiliza o diálogo nem altera o conteúdo do `DialoguePlan`;
mantém a barreira da produção. No caso `adult_relationship_subtext`,
permite investigar a falha do Groq de forma mais específica:

```text
providers certify social --suite nuance4 --case adult_relationship_subtext
```

Uma falha de fechamento não comprova bloqueio de assunto adulto. Também não
é evidência de inferioridade semântica: respostas não validadas não são
exibidas e não recebem score de qualidade. Os resultados precisam de
revisão humana e não promovem providers.


### Groq HTTP 400 — categoria segura de rejeição (CH3)

Um `PROVIDER_HTTP_400` ocorre **antes** da validação pelo Core e,
isoladamente, **não prova censura**. A API Groq pode recusar requisições por
parâmetros não suportados, limite de contexto ou falhas ao gerar JSON no
modo `response_format=json_object`; conteúdo é apenas uma hipótese a
investigar se o próprio provider devolver identificação explícita.

Depois de atualizar a aplicação, execute **somente o caso com falha**:

```text
providers certify social --suite nuance4 --case adult_relationship_subtext
```

A certificação mostrará `transporte: fase=http_request_rejected · HTTP=400 ·
categoria=<classificação>`, com apenas categorias fixas:

- `json_generation_failed`: assinatura reconhecida de falha de JSON;
- `explicit_content_policy_rejection`: o serviço declarou explicitamente filtro/política;
- `unsupported_parameter`: argumento rejeitado pelo serviço;
- `context_length_exceeded`: contexto acima do limite;
- `model_unavailable`: código explícito de modelo indisponível;
- `invalid_request_unspecified`: requisição inválida sem causa específica;
- `unclassified`: resposta sem diagnóstico confiável.

**Atenção:** classificação é conservadora e best-effort; texto da exceção,
mensagem bruta de erro e prompt nunca são publicados. A ausência de uma
categoria específica significa informação insuficiente, não ausência de
filtros. O HTTP 400 não é repetido automaticamente; testes read-only não
promovem modelo nem mudam o validador. Corrigir o erro deve se basear na
categoria observada, não em mudar guardrails às cegas.

O baseline Groq/Qwen usa `response_format={"type":"json_object"}`,
`reasoning_effort="none"` e mantém `temperature=0.70`. A documentação do
Groq descreve que o modo JSON Object ocasionalmente pode falhar e retornar
erro. Migrar para JSON Schema estrito é possibilidade **se confirmada
compatibilidade**, mas deve ocorrer em PR separado, com A/B e regressões.


### Diagnóstico Groq isolado: sem chamada NVIDIA

Para investigar o HTTP 400 sem gastar tempo e quota do Mistral:

```text
providers diagnose groq --suite nuance4 --case adult_relationship_subtext
```

Essa sonda executa **apenas o Groq/Qwen**, uma única vez, com o mesmo
ContextCompiler/DialoguePlan e histórico ficcional da Nuance4. Timeout do
job: 65s (Groq usa timeout próprio de 30s para a realização). Não escreve
histórico, estado ou memória. Não revela **nem mesmo fala validada**:
retorna `validated`, `latency_ms`, `error_code`, categoria de erro segura
e `validation_failure_detail` quando aplicável.

O comando exige `--case` (um teste só), aceita corpora fechados
`nuance4`, `expressive4` e `full11`, e precisa apenas de `GROQ_API_KEY`.
Não tenta contornar recusas, não repete HTTP 400 automaticamente, não toca
na decisão de promoção e não aciona a NVIDIA. Se quiser comparar as
**falas naturais** depois, execute a certificação A/B usual.


### Perguntas reflexivas em contexto — sem liberar follow-up genérico

Uma falha anterior da `SOCIAL_NUANCE4_V1` em
`adult_relationship_subtext` foi identificada com precisão pelo comando
`providers diagnose groq`: `FOLLOWUP_NOT_AUTHORIZED` com
`validation_failure_detail=terminal_question_mark`. Isso indica que o
Groq terminou uma fala com `?`, e não que recusou o conteúdo sexual.
O texto da fala rejeitada não foi capturado no relatório; não é
possível julgar sua qualidade semântica.

O Core agora reconhece `confuso/confusa` como estado de desabafo.
Quando existe histórico de conversa pertinente **e** o interlocutor
pergunta explicitamente se Amora entendeu/sacou a ideia, o DialoguePlan
pode declarar `allow_reflective_question=true` e
`closing_mode=natural`, mantendo `ask_followup=false`. O realizador
fica livre para concluir em afirmação ou uma pergunta curta que confira
a interpretação. O validador aceita **somente uma interrogação final**
dentro dessa autorização e de atos `respond_to_disclosure` ou
`answer_question`; ainda rejeita pergunta extra, `ask_followup`
fora do plano, oferta de ajuda não solicitada, moves não autorizados,
textos muito longos e falas fora do perfil/contexto. Isso não garante
semanticamente que toda pergunta permitida seja boa: continua necessária
revisão humana.

**Não** aplica uma permissão geral para perguntas, não altera dados
privados ou o PrivacyGate, não força interrogação nem altera o provider
promovido. Teste recomendado após o deploy:

```text
providers diagnose groq --suite nuance4 --case adult_relationship_subtext
```

Se `validated=true`, execute uma comparação A/B apenas desse caso,
para analisar a qualidade da resposta e não só o contrato:
`providers certify social --suite nuance4 --case adult_relationship_subtext`.


### Mistral Large 4 — modelo principal de conversa social (ADR-0036)

**Decisão explícita do responsável pelo projeto, 2026-11-04:** a Amora
usa Mistral Large 4 na NVIDIA como realizador primário de
`TaskClass.SOCIAL_DIALOGUE`. O Groq/Qwen permanece **fallback**. Não
altera raciocínio técnico, personalidade e estado canônico do Core,
permissões, privacidade, memória ou validação de fala.

O Northflank deve manter `NVIDIA_API_KEY` como segredo
(`AMORA_SECRET_NVIDIA_API_KEY` no ambiente, ou no SecretStore). Depois
do deploy de `main`, execute o comando `status` no Shell e verifique
`social_primary_provider: nvidia_mistral_large_4`.
Se a chave NVIDIA não estiver disponível, o Groq/Qwen continua como
primário elegível, sem falhar a inicialização.

A configuração é:

```text
AMORA_SOCIAL_PRIMARY=mistral  # padrão, se houver NVIDIA_API_KEY
AMORA_SOCIAL_PRIMARY=groq     # rollback operacional
```

Aplicar mudanças requer restart/redeploy. O valor desconhecido falha
explicitamente durante bootstrap (sem seleção silenciosa).
O adapter produtivo é separado do candidato de certificação e usa
`reasoning_effort=low`, limite absoluto de 95s, limite de inatividade
de 55s e **sem segunda tentativa silenciosa** no mesmo provider.
Se houver falha, o fallback é executado pelo Core antes de liberar fala.
O estado de produção não promete JSON Schema estrito do endpoint NVIDIA:
todos os frames JSON continuam **validados localmente** antes da voz.

O Shell `providers certify social` preserva seu comportamento de
comparação isolada Groq × Mistral e `promotion_decision=none`;
isso significa apenas que **a certificação não promove automaticamente**.
A promoção real aqui decorre de decisão do proprietário registrada
separadamente na ADR-0036.

**Trade-off aceito:** a latência real de Mistral pode chegar a dezenas
de segundos ou ultrapassar um minuto. Proxies HTTP e budgets da
superfície podem expirar antes do limite do adapter. Isso não muda
identidade nem autoriza reproduzir texto incompleto na voz.


### Correção da observabilidade do Shell `status` (CH3)

O `status` administrativo antes expunha **somente** `core.readiness()`:
`{"status":"ready","storage":"libsql"}`. O campo
`social_primary_provider` estava no `AmoraCore.health_report()`, mas
não no resultado do comando do Shell. Portanto, o campo ausente
**não indicava** que Mistral estivesse inativo.

A partir desta correção, `status` mantém o `readiness` existente e,
quando houver roteador de cognição, adiciona:

- `social_primary_provider`: primeiro provider do ranking social configurado;
- `social_fallback_providers`: nomes dos demais providers elegíveis;
- `social_routing_scope="configured_personal_remote"`: informa que o
  ranking supõe uma conversa `PERSONAL` com processamento remoto
  autorizado e saída estruturada.

Para confirmar no Northflank após deploy do `main`:

```text
status
```

Com `AMORA_SOCIAL_PRIMARY=mistral` (padrão) e chave NVIDIA presente,
`social_primary_provider` deverá ser `nvidia_mistral_large_4`.
O status não expõe tokens, prompt, conversas, perfis ou corpo de
respostas. Esse valor descreve o **ranking configurado**, não garante
que uma mensagem específica use Mistral: privacidade, limites do
contexto, circuit breaker, quota e timeouts podem mudar a escolha
ou provocar fallback em cada turno.

### Laboratório Mistral A/B/C — comando de operador (ADR-0037)

O comando `providers experiment mistral-abc [--case vampiro|rpg]` é uma bateria
isolada de **três chamadas sequenciais** à NVIDIA para um caso fictício:
A (Core), B (prompt arquivado + Core) e C (somente prompt arquivado).
O caso padrão é `vampiro`. Os argumentos são fechados; não aceitam texto de
usuário, caminho de arquivo nem execução arbitrária.

O arquivo `jailbreak/Mistral-Jailbreak.md` é empacotado em modo somente
leitura no runtime e validado com SHA-256 antes do experimento. A e B usam
o mesmo contexto sintético e `RealizationValidator`; C é diagnóstico sem
autorização de fala pela Amora. O Shell apresenta progressão A/B/C, tempos,
validade estrutural e, quando autorizadas, falas para revisão humana.
Não há mutação de memória, estado, prompt da produção ou promoção.

O job exige autorização de operador e tem teto de 650s. Cada request do
adapter mantém seus limites de 55s de inatividade real e 185s totais;
limites HTTP 429 encerram a bateria. Precisa de `NVIDIA_API_KEY` no
runtime da Amora, não apenas no secret do GitHub Actions. **Código e
testes offline não representam execução live da bateria.**


### Experimento Amora Virtual Human V2 (operator-only; sem promoção)

Comando adicional de laboratório:

```text
providers experiment amora-v2-ab --case mente_poluida
```

Usa exclusivamente o overlay inglês `jailbreak/Amora-Virtual-Human-V2.md`
e o Mistral Large 4 NVIDIA, comparando Core canônico × Core+V2 com
snapshot sintético congelado e duas chamadas sequenciais sem fallback.
Os novos casos de malícia contextual, provocação, palavrões e humor
adulto estão em `core/amora/cognition/amora_v2_experiment.py`.
Argumentos são fechados e exigem `operator`; resultados são cegos
para revisão humana, validados pelo `RealizationValidator` e nunca
persistem self-facts, memórias ou decisões de promoção.

A tradução PT-BR do prompt fica apenas na conversa com o operador;
não é artefato de deployment. A validação offline e o CI não substituem
a certificação live NVIDIA. O experimento não altera o provider principal.
Procedimento e limites: `docs/experiments/AMORA_V2_AB.md`.
