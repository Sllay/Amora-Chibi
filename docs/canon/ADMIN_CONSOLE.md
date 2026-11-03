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
