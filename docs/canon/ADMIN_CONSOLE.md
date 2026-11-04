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

O adaptador candidato NVIDIA usa `stream=true` via SSE, agrega
`choices[].delta.content` até concluir o JSON e ignora
`reasoning_content`. O prazo de **55 segundos é total por chamada**
(`asyncio.timeout`), não reiniciado quando chegam tokens. Streaming
não garante redução da latência total nem conclusão em 55 segundos.
A certificação inteira ainda tem limite de job de 300 segundos,
independente dos deadlines individuais. Falhas não promovem provider.

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
os providers e não promove nenhum automaticamente. O adaptador Mistral mantém
`reasoning_effort=high`, `max_tokens>=4096`, SSE e deadline **total** de 55s,
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
`max_tokens>=4096`, limite total de 55 segundos e o mesmo
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
