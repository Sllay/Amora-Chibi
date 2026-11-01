# ADR-0032 — Platform messaging lifecycle contract

Status: **PROPOSED**  
Data: 2026-11-01

## Contexto / problema

O staging físico do Discord expôs uma sequência de falhas que individualmente pareciam bugs independentes:

- binding Discord salvo como `user_id` enquanto o worker apresentava `discord_user_id`;
- `privacy_scope` com casing incompatível;
- slash command concluindo o defer, mas expirando numa consulta curta ao Core;
- respostas explicitamente endereçadas sendo descartadas por reply lease menor que a latência real do provider;
- processamento remoto desabilitado por política gerando apenas fallback local;
- depois de habilitar processamento remoto, o worker e o provider passaram a operar com deadlines incompatíveis;
- durante deploy/restart do Core, o worker fez polling agressivo e recebeu rajadas de `503`;
- o Core reinicia a presença transitória como `disconnected`, mas um socket Discord pode sobreviver e precisar se reconciliar;
- transcript literal recente era consultado globalmente no `CognitiveStore`, apesar de `ConversationMessage` já persistir `session_id`.

A causa sistêmica não é um "Discord brain" separado. O desenho correto já existe: o worker normaliza fatos de transporte e o Core decide identidade, atenção social, diálogo, contexto, provider e outbound. O problema é que o lifecycle distribuído `Gateway → worker → Core → provider mesh → Core → Discord REST` não tinha um contrato único de sessão, deadline e idempotência.

## Evidência arquitetural

O fluxo canônico permanece:

```text
Discord Gateway
  ↓
worker Discord (transporte)
  ↓ InboundMessage
Core SocialAttentionRouter
  ↓
CognitiveRequest
  ↓
ContextCompiler + DialogueOrchestrator + Memory + World + Emotion
  ↓
provider mesh estruturado (quando permitido)
  ↓
MessagingStore outbound
  ↓
worker Discord
  ↓
Discord REST
```

O worker não ganha autoridade para resolver pessoa, relação, memória, emoção, objetivo social ou resposta.

## Falhas de fronteira confirmadas

### 1. Transcript literal sem isolamento de sessão

`ConversationMessage.session_id` já existia, porém `ContextCompiler` lia as mensagens recentes sem filtro de sessão. Assim, fala literal Web/canal A/DM poderia aparecer no contexto recente de canal B se passasse pelos demais filtros de privacidade.

Memória e World Model são mecanismos canônicos de continuidade entre sessões. Transcript literal não é.

### 2. Relógios independentes e incompatíveis

Antes deste contrato, um turno podia ter simultaneamente:

- timeout HTTP do worker para Core: ~20 s;
- timeouts individuais de providers: 30–45 s ou mais;
- vários providers tentados sequencialmente;
- reply lease direta: 90 s.

Nenhuma camada sabia quanto tempo restava para a próxima. Um provider podia continuar trabalhando depois que o worker já desistiu ou consumir a janela inteira da autorização social.

### 3. Dedupe de audit não era idempotência de processamento

O audit inbound era persistido antes da cognição. Se a requisição morresse depois desse ponto e o worker repetisse a mesma mensagem, o Core podia tratá-la como `DUPLICATE_PLATFORM_MESSAGE` e não terminar a resposta.

### 4. Presença persistente e socket físico podiam divergir

`desired_connected` é persistente, mas o Core deliberadamente reseta estados transitórios após restart. Um worker cujo socket Discord continua pronto precisa reconciliar esse fato com a nova geração do Core. `check_requested` também precisa concluir mesmo quando o socket já estava conectado antes do pedido.

### 5. Invalidação de transporte era ampla demais

Um `MESSAGE_UPDATE` não semântico ou uma atualização da própria mensagem do bot podia alterar geração local/Core e cancelar uma resposta humana em andamento.

## Decisão

### A. Transcript literal é session-scoped

`CognitiveStore.list_messages()` aceita `session_id` e o `ContextCompiler` usa o `request.session_id` para `recent_messages`.

Regras:

- Web, DM, canal e thread diferentes não compartilham transcript literal automaticamente;
- Memory e World Model continuam podendo fornecer continuidade canônica entre superfícies;
- AUTH/SECRET e item-level remote flags continuam obrigatórios;
- requests legadas sem `session_id` preservam o comportamento antigo explicitamente marcado como legado.

### B. Uma mensagem possui um lifecycle determinístico

Para cada `(platform, account_scope, conversation_id, message_id)`:

- `turn_id` cognitivo é determinístico por UUIDv5;
- outbound lógico é determinístico por UUIDv5;
- retries no mesmo processo juntam a mesma task protegida por `asyncio.shield`;
- retry após restart consulta audit/outbound persistidos e reutiliza resultado terminal ou retoma somente um turno incompleto;
- duplicate delivery nunca deve criar um segundo outbound lógico.

### C. Deadline ladder único

O contrato padrão de platform messaging passa a ser:

```text
provider attempt budget      = 12 s
provider total failover      = 20 s
Core cognition hard timeout  = 24 s
worker inbound HTTP timeout  = 30 s
explicit social reply lease  = 90 s
```

Invariante:

```text
provider attempt
  ≤ provider total budget
  < Core hard timeout
  < worker HTTP timeout
  < social stale lease
```

O provider mesh lê os budgets do `CognitiveRequest.metadata` e limita cada `realize()` pelo tempo restante. Outras superfícies que não fornecem esses campos mantêm seu comportamento anterior.

Se o budget remoto termina, a resposta local de fallback ainda deve ser enfileirada. Timeout não vira silêncio.

### D. Retry é restrito a falha de transporte/transiente

O worker pode repetir o mesmo inbound somente para falhas transitórias como rede e HTTP `502/503/504`, sempre preservando exatamente o mesmo envelope e `message_id`.

Erros de contrato/autorização (`400/401/403/409/422`) não entram em retry cego.

### E. Discord Create Message usa idempotência nativa

Cada chunk de um outbound recebe `nonce` estável derivado do outbound lógico e do índice do chunk, com `enforce_nonce=true`.

Isso faz retry, reclaim de lease e rolling deploy reapresentarem a mesma operação de criação ao Discord, em vez de criarem uma nova mensagem quando o nonce ainda está na janela de unicidade do Discord.

`allowed_mentions` continua fechado por padrão.

### F. Claim de outbound começa perto do envio

O worker reclama um outbound por vez, com lease explícita e limitada, em vez de reclamar um lote cujas leases envelhecem enquanto os primeiros itens são enviados.

### G. Presença é reconciliável após restart

Quando o socket Discord está `READY` mas o Core foi reiniciado:

- worker lê a geração atual;
- se necessário reporta `connecting → connected` usando a geração atual;
- um `409` por geração provoca uma única leitura fresca/reconciliação, não um hot loop;
- `check_requested` é concluído mesmo com socket previamente conectado;
- falha de presença não impede registro do slash command nem o estado Gateway `READY`;
- polling de presença usa backoff exponencial com jitter em falhas transitórias.

### H. Só mutação semântica invalida resposta

O runtime Discord trata como idempotentes:

- `MESSAGE_CREATE` duplicado;
- update sem mudança nos campos consumidos pela normalização;
- update/delete conhecido como mensagem do próprio bot.

Mudança de conteúdo, menções, anexos ou referência/reply continua criando nova revisão e invalida o outbound ancorado na revisão anterior.

## Matriz de comportamento esperado

| Situação | Resultado |
| --- | --- |
| menção direta + provider saudável | `respond`, provider remoto, `outbound=1` |
| provider lento/indisponível | failover dentro do budget; depois fallback local, `outbound=1` |
| worker perde resposta HTTP após Core começar | Core continua task; outbound determinístico fica na fila |
| worker repete mesmo inbound | junta/reutiliza o mesmo lifecycle; sem segunda resposta lógica |
| Core retorna `503` durante deploy | retry com backoff; sem hot loop |
| Core reinicia, socket Discord sobrevive | presença é reconciliada para geração atual |
| update não semântico do Discord | não invalida turno |
| mensagem do próprio bot recebe update/delete | não invalida turno humano |
| mensagem humana é editada/deletada enquanto Amora pensa | revisão/floor invalidam resposta antiga |
| ACK/outbound é reapresentado | nonce Discord evita criação duplicada na janela suportada |
| canal B conversa após canal A/Web | transcript literal de A/Web não entra em `recent_messages` de B |

## Observabilidade

Sem conteúdo privado ou IDs crus em logs normais, o Core/worker devem permitir distinguir:

- ação de atenção;
- quantidade de outbound;
- duplicate/recovery;
- `remote_processing_allowed`;
- provider/model;
- fallback usado;
- reason codes cognitivos;
- timeout/budget;
- presença/reconciliação;
- retry e erro HTTP estável.

## Consequências

Positivas:

- staging deixa de depender de tentativa e erro por camada;
- falha transitória não muda o significado lógico da mensagem;
- raw transcript ganha fronteira explícita de privacidade;
- failover não pode consumir indefinidamente a autorização social;
- rolling deploy/retry têm proteção contra resposta duplicada;
- presença Web reflete melhor o transporte real após restart.

Custos:

- lifecycle de mensageria fica mais explícito e possui mais testes de integração;
- provider muito lento será abandonado antes do timeout próprio da biblioteca nesta superfície;
- nonce do Discord tem janela de unicidade limitada pelo próprio serviço e não substitui audit/outbound persistentes;
- exatamente-once absoluto entre dois sistemas distribuídos continua impossível sem cooperação do provedor; a meta é idempotência prática e auditável.

## Certificação obrigatória antes de ACCEPTED

1. Core tests Python 3.11–3.14 verdes.
2. Messaging worker tests verdes.
3. Cloud/container e pre-deploy security verdes.
4. Regressão de isolamento de sessão.
5. Regressão de provider budget/fallback.
6. Regressão de duplicate inbound/outbound determinístico.
7. Regressão de create/update/delete Discord idempotente.
8. Staging físico:
   - `@Amora` produz provider real ou fallback explícito, nunca silêncio por deadline;
   - Core restart com socket sobrevivente reconcilia presença;
   - deploy `503` não perde mensagem nem cria duplicata;
   - edit/delete humano invalida resposta velha;
   - mensagem do bot não invalida turno humano.

Após CI e staging físico, alterar este ADR para **ACCEPTED** e atualizar `PROJECT_STATE.md`.
