# AMORA — Platform Messaging / Social Attention

**Status:** CANÔNICO / ATIVO  
**Atualizado em:** 2026-10-31

## 1. Princípio

WhatsApp, Discord e futuras superfícies de mensagem são transportes da mesma Amora. A plataforma não define identidade, parentesco, memória ou autoridade cognitiva.

```text
Platform Worker
    ↓ normalize
InboundMessage
    ↓
PlatformIdentityBinding
    ↓
SocialAttentionRouter + ConversationFloor
    ↓
Core-owned control/cognition
    ↓
OutboundMessage queue
    ↓
Platform Worker
```

`external platform id != PERSON` permanece invariante.

## 2. Contrato normalizado

O Core recebe fatos de transporte, não objetos nativos de SDK. `InboundMessage` carrega, entre outros:

- `platform` / `account_scope`;
- `conversation_id`;
- `message_id` como delivery/dedupe id;
- `native_message_id` quando a plataforma possui objeto revisável;
- `transport_revision`;
- `channel_type` (`dm` ou `group`);
- um ou mais `sender_identifiers` estáveis;
- texto e metadados de attachments;
- mentions;
- reply/quote nativo;
- timestamp e privacy scope.

O worker não informa `speaker_entity_id` canônico. O Core resolve isso via `PlatformIdentityBinding`.

A saída usa `OutboundMessage`, incluindo `conversation_id`, texto, `source_message_id` e `reply_to_message_id`. Adapters devem preferir a primitiva nativa de reply/quote.

## 3. DM: gate antes da cognição

DM de pessoa desconhecida não entra no Cognitive Core normal nem em parsers de controle operacional.

```text
DM chega
→ adapter extrai envelope/ID
→ PlatformIdentityBinding
   ├─ conhecido → elegível a responder/controlar conforme capability
   └─ desconhecido → QUARANTINE
                     ├─ audit privado
                     └─ NÃO CognitiveStore/provider/control parser
```

O audit privado pode conservar o texto recebido para segurança/operator review. Isso não transforma o conteúdo em memória da Amora.

## 4. Grupo: contexto social não significa turno

Mensagens de grupo podem contribuir para contexto recente mesmo quando Amora não responde.

Evidência de atenção, em ordem aproximada:

```text
native reply/quote para Amora    1.00
native @mention da Amora         0.995
vocativo explícito "Amora, ..." 0.98
mesmo speaker no floor ativo     0.82, bounded
ambiente sem atenção direta      context-only
menção explícita a outra pessoa  shift away / context-only
```

Incerteza favorece silêncio.

Pessoa desconhecida no grupo pode falar diretamente com Amora; ela recebe pseudônimo efêmero para o turno/contexto, não uma `PERSON_*` canônica automática.

## 5. Conversation Floor bounded

Atenção de grupo não fica presa indefinidamente.

- floor direto: janela curta;
- continuidade implícita não renova o lease original sem nova evidência forte;
- outro participante assumindo o turno incrementa `generation` e invalida reply antigo;
- antes de enfileirar resposta gerada, o Core revalida generation/lease;
- edit/delete nativo também invalida generation;
- se o grupo virou de assunto enquanto o provider pensava, a resposta velha é suprimida.

## 6. Revisão/delete de mensagem

Plataformas com objeto de mensagem revisável podem apresentar múltiplas entregas para a mesma source nativa.

No Discord:

```text
native message id = M

create  → delivery id M,        revision 1
edit #1 → delivery id M#edit:2, revision 2
edit #2 → delivery id M#edit:3, revision 3
```

`native_message_id` permanece `M`, portanto reply/cancel continuam ancorados ao objeto correto da plataforma.

Antes de reprocessar uma edição, o worker envia `TransportInvalidation` sem body de mensagem. O Core:

1. incrementa/limpa o ConversationFloor do canal;
2. cancela outbound queued/claimed ligado ao native source;
3. permite que a nova revisão seja avaliada como nova entrega.

O worker mantém cache bounded recente e executa uma segunda validação de source/revision antes do send. Isso fecha a corrida em que a source muda depois que o Core já enfileirou resposta.

`MESSAGE_DELETE` e bulk delete tornam a source inválida e revogam respostas pendentes.

## 7. Reactions

Reaction é evidência social de transporte, não utterance textual.

O Discord worker pode manter add/remove no cache bounded, mas não deve fabricar fala como `"fulano reagiu com coração"` e alimentar cognition/provider automaticamente.

Uma futura semântica de reactions precisa de contrato estruturado no Core.

## 8. Audit/History != Memory

`messaging_audit` é histórico operacional/social para segurança e investigação.

Ele conserva de forma privada o necessário para reconstruir o que ocorreu:

- plataforma/account/conversation/message;
- direção inbound/outbound;
- sender externo e PERSON resolvida quando houver;
- timestamp;
- texto;
- reply/mention metadata;
- routing action/reason codes;
- se Amora respondeu;
- hashes/revision metadata quando necessário para transporte.

Não existe consolidação automática de toda mensageria em `Memory`, World Model ou Relationship Graph.

Requests originados da camada de mensageria carregam explicitamente:

```text
memory_consolidation = false
relationship_learning = false
messaging_audit = true
```

## 9. Privacidade de IDs

Raw WhatsApp/Discord IDs são dados privados de runtime.

- não entram em health report;
- não entram no CognitiveRequest literal;
- conversation/message refs cognitivos são hashes/refs internos;
- operator audit mascara IDs por padrão;
- reveal exige papel `operator` explícito;
- worker logs não imprimem mensagens, tokens, JIDs ou auth state.

Nickname/display name é apresentação contextual. Nunca substitui ID estável como identidade forte.

## 10. Worker credential

Workers externos usam `AMORA_MESSAGING_SERVICE_TOKEN`.

Esse token:

- é distinto do admin;
- é distinto do voice service token;
- em cloud exige pelo menos 32 bytes;
- acessa somente `GET/POST /v1/messaging/worker/*`;
- pode entregar inbound, invalidar source, claim/cancel/ack outbound e reportar transporte;
- não acessa `/v1/state`, Memory, World, Actions, Pessoas, Console ou operator APIs.

Comprometer um worker não deve equivaler a comprometer o Core administrativo.

## 11. PresenceController

Presença desejada e transporte físico são estados diferentes.

O Core persiste:

```text
desired_connected
check_requested
generation
```

O worker reporta o transporte:

```text
DISCONNECTED
→ CONNECTING
→ CONNECTED
↔ CHECKING
→ DISCONNECTING
→ DISCONNECTED
```

Ao reiniciar o Core, qualquer estado físico anterior é resetado para `DISCONNECTED` com `CORE_RESTART_TRANSPORT_UNKNOWN`, mas `desired_connected` permanece. Um facade secundário usado por policy não repete esse reset de startup.

Um `CHECK` pode fazer connect/check/disconnect bounded quando `desired_connected=false`.

## 12. Autoridade de presença por PERSON

Controlar presença operacional é uma capability própria do profile:

```text
can_control_platform_presence
```

Ela é diferente de `can_define_amora_family`.

```text
stable external actor id
→ PlatformIdentityBinding
→ PERSON_*
→ can_control_platform_presence?
   ├─ sim → pode executar controle explícito
   └─ não → nenhuma mutação
```

Discord role, guild admin, nickname e display name não concedem essa autoridade.

Instalação nova seedada:

- `PERSON_BK`: `true`;
- `PERSON_LUCAS`: `false`;
- `PERSON_NICOLAS`: `false`.

O catálogo continua bootstrap-only. Perfil persistido existente não recebe grant novo silenciosamente. Operator pode conceder/revogar explicitamente e com confirmação.

## 13. Controle natural bounded

Comando operacional explícito pode ser reconhecido deterministically no Core antes do provider:

```text
Amora, conecta no Discord
Amora, desconecta do Discord
Amora, checa o Discord
status do Discord
```

Regras:

- somente após identity/social gate;
- mutação exige `can_control_platform_presence`;
- resposta local não usa provider;
- não consolida Memory automaticamente;
- frase ambígua continua cognição normal;
- termos `call`, `chamada`, `voz`, `voice`, `canal de voz` excluem o parser textual.

Assim `Amora, entra na call do Discord` nunca é confundido com “ligar o worker textual”.

## 14. Discord Social Text Runtime

Identidade forte: Discord user ID.

Worker usa Gateway v10 + REST v10 e preserva:

- author user ID;
- guild/channel/message IDs;
- native mentions;
- message reference/reply;
- attachment metadata;
- revision/delete state bounded;
- reaction transport evidence.

Intents textuais atuais:

- `GUILDS`;
- `GUILD_MESSAGES`;
- `GUILD_MESSAGE_REACTIONS`;
- `DIRECT_MESSAGES`;
- `DIRECT_MESSAGE_REACTIONS`;
- `MESSAGE_CONTENT`.

Guild Members e Guild Presences não são pedidos pelo runtime textual atual.

Para texto normal em guilds, o bot precisa do `MESSAGE_CONTENT` intent conforme regras atuais do Discord. Revalidar o processo de privileged-intent antes de expansão pública.

Mensagens de bots/self são descartadas como fala humana.

### Gateway recovery

- heartbeat + ACK;
- Resume com session id/sequence quando válido;
- lost ACK força reconnect;
- close não-resumível limpa resume state;
- close terminal de auth/config/intents/sharding entra em ERROR e evita reconnect storm.

### REST

- `429`: respeitar retry timing fornecido pelo Discord;
- `5xx`/network/timeout: retry bounded;
- `401/403/404`: terminal por operação;
- nunca contornar rate limit.

### Mentions

Outbound nega mentions por padrão:

```text
allowed_mentions.parse = []
replied_user = false
```

Mention futura precisa ser estruturada pelo Core.

## 15. WhatsApp linked-device

Para a semântica desejada de conta/número normal participando de grupos comuns, existe worker isolado Baileys. Esse caminho é não-oficial em relação à Meta Cloud API e mantém risco operacional/de conta.

Regras atuais:

- Baileys diretamente pinado em `7.0.0-rc14`;
- `messages.upsert` só é aceito quando `type=notify`;
- evento com `requestId` é descartado como defesa adicional ao advisory de spoof/historical sync;
- full history sync desabilitado;
- auth state nunca vai para Git/log;
- disconnect normal usa `end`, nunca `logout`;
- reply nativo usa quote da mensagem original quando ainda existe no cache bounded do worker;
- PN JID/LID/telefone são apenas evidências para bindings; nunca PERSON por si.

O worker atual usa `useMultiFileAuthState` para bootstrap/staging. Antes de declarar deploy definitivo, migrar auth para backend robusto/durável e provar reconnect/sync com conta real.

## 16. Status físico

Implementação do contrato/Core/workers não equivale a prova de conectividade real.

Ainda exigem staging físico:

- Discord Application/Bot/token real;
- Message Content/intents/permissões reais;
- edit/delete/reconnect/rate-limit em rede real;
- conta WhatsApp real, linked-device pairing e reconnect/history behavior;
- armazenamento produtivo do auth state WhatsApp.

Até o gate de cada plataforma passar, código é `IMPLEMENTED / STAGING PENDING`, não “conectado em produção”.

## 17. Discord Voice é subsistema separado

Discord Voice/Call não deve ser implementado dentro do worker textual. Voice Gateway, transporte de mídia, speaker streams, codec/cripto, VAD/STT/TTS, barge-in e ConversationFloor de voz possuem lifecycle próprio e precisam de ADR/staging separados.
