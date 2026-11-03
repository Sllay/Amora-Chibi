# ADR-0035 — Amora Admin Console / Command Bus

- Status: **ACCEPTED**
- Date: 2026-11-03
- Scope: administrative operations / Living Console / CH3 live certification access

## Contexto / problema

CH3 já possui o runner canônico `run_live_social_ab()` para executar o A/B live Qwen × Mistral, mas a operação inicial era acessível apenas por CLI (`python -m amora.cognition.certification_live`).

Em infraestrutura cloud, o shell interativo do host pode ser indisponível, bloqueado, desconfortável no celular ou mudar completamente quando a hospedagem muda. Tornar o painel do host requisito operacional acoplaria a Amora à infraestrutura e enfraqueceria a portabilidade definida pelo ADR-0015.

A Amora já possui Living Console Web e credencial de operador. Faltava uma fronteira oficial para comandos administrativos longos e auditáveis.

## Alternativas consideradas

### A. Abrir bash/shell Linux pela Web

Rejeitado.

Motivos:

- aumenta drasticamente a superfície de ataque;
- exige sanitização impossível de tornar equivalente a um registry explícito;
- permite acessar recursos fora do ownership da Amora;
- acopla operação ao sistema operacional e à hospedagem;
- conflita com observabilidade estruturada.

### B. Criar endpoints ad hoc para cada botão

Viável para poucos casos, mas rejeitado como arquitetura principal.

Motivos:

- repete autenticação, timeout, status e histórico;
- não oferece linguagem operacional consistente;
- incentiva crescimento desorganizado do painel.

### C. Command Registry + job bus autenticado

Aceito.

## Decisão

Criar o **Amora Command Bus**, acessível por API administrativa autenticada e por uma UI chamada **Amora Shell**.

O Shell interpreta somente paths registrados, nunca texto arbitrário do sistema operacional.

Cada comando registra:

```text
path
usage
description
permission
timeout
handler
exclusive/non-exclusive
```

Comandos longos são executados como jobs internos:

```text
queued → running → succeeded | failed | timed_out
```

O `POST` retorna `202 Accepted` e `job_id`; o cliente acompanha o job separadamente. Isso evita depender de uma conexão HTTP longa durante certificações live.

## Primeiro comando de valor real

```text
providers certify social
```

Esse comando reutiliza exatamente `run_live_social_ab(core)`; ele não cria outro benchmark nem outro caminho de provider.

Mantém obrigatoriamente:

- `SOCIAL_SURFACE_REPLAY_V1`;
- canonical Context Compiler;
- contexto read-only;
- execução sequencial;
- Qwen baseline × Mistral candidate;
- nenhuma promoção automática;
- revisão humana obrigatória.

Duas certificações sociais simultâneas são bloqueadas no Command Bus para evitar burst de quota e resultados concorrentes desnecessários.

## Segurança

O Admin Command Bus:

- exige role `operator` ou `admin`;
- não executa `eval`, `exec` ou shell arbitrário;
- não recebe valores de secrets como argumentos;
- não retorna valores de secrets em erros;
- limita histórico em memória;
- aplica timeout por comando;
- converte falhas conhecidas em error codes estruturados.

## Consequências

Positivas:

- CH3 live pode ser certificado pelo celular sem abrir shell do host;
- operação administrativa passa a pertencer à Amora;
- trocar Northflank/Koyeb/host não muda a interface administrativa;
- novos comandos podem reutilizar autenticação, jobs, timeout e histórico;
- resultados ficam fáceis de apresentar tanto em UI quanto em JSON.

Custos/limites atuais:

- histórico de jobs é transitório e some em restart;
- progresso inicial é por polling do estado do job, não streaming por caso;
- o registry inicial possui apenas `help`, `status` e `providers certify social`.

Esses limites são aceitáveis para a primeira entrega e não alteram memória cognitiva ou storage schema.

## Critério para revisitar

Revisitar quando:

- jobs precisarem sobreviver restart/deploy;
- múltiplas instâncias exigirem coordenação distribuída;
- progresso por caso justificar SSE/WebSocket/event stream;
- auditoria administrativa persistente se tornar requisito de segurança.

Mesmo nesses casos, preservar o registry explícito e continuar proibindo shell arbitrário como contrato padrão.
