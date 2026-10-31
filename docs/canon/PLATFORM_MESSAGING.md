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
Cognitive Core
    ↓
OutboundMessage queue
    ↓
Platform Worker
```

`external platform id != PERSON` permanece invariante.

## 2. Contrato normalizado

O Core recebe fatos de transporte, não objetos nativos de SDK. `InboundMessage` carrega, entre outros:

- `platform` / `account_scope`;
- `conversation_id` / `message_id`;
- `channel_type` (`dm` ou `group`);
- um ou mais `sender_identifiers` estáveis;
- texto e metadados de attachments;
- mentions;
- reply/quote nativo;
- timestamp e privacy scope.

O worker não informa `speaker_entity_id` canônico. O Core resolve isso via `PlatformIdentityBinding`.

A saída usa `OutboundMessage`, incluindo `conversation_id`, texto e `reply_to_message_id`. Adapters devem preferir a primitiva nativa de reply/quote ao responder em grupos.

## 3. DM: gate antes da cognição

DM de pessoa desconhecida não entra no Cognitive Core normal.

```text
DM chega
→ adapter extrai envelope/ID
→ PlatformIdentityBinding
   ├─ conhecido → elegível a responder
   └─ desconhecido → QUARANTINE
                     ├─ audit privado
                     └─ NÃO CognitiveStore/provider
```

Isso impede que um desconhecido use DM para conversar, redefinir família ou alimentar contexto cognitivo antes de ser autorizado.

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
- antes de enfileirar resposta gerada, o Core revalida a generation/lease;
- se o grupo virou para outro assunto enquanto o provider pensava, a resposta velha é suprimida.

## 6. Audit/History != Memory

`messaging_audit` é histórico operacional/social para segurança e investigação.

Ele conserva de forma privada o necessário para reconstruir o que ocorreu:

- plataforma/account/conversation/message;
- direção inbound/outbound;
- sender externo e PERSON resolvida quando houver;
- timestamp;
- texto;
- reply/mention metadata;
- routing action/reason codes;
- se Amora respondeu.

Não existe consolidação automática de toda mensageria em `Memory`, World Model ou Relationship Graph.

Requests originados da camada de mensageria carregam explicitamente:

```text
memory_consolidation = false
relationship_learning = false
messaging_audit = true
```

Uma policy futura de aprendizado social pode selecionar evidências de mensagem, mas deve ser explícita e separada do audit bruto.

## 7. Privacidade de IDs

Raw WhatsApp/Discord IDs são dados privados de runtime.

- não entram em health report;
- não entram no CognitiveRequest literal;
- conversation/message refs cognitivos são hashes/refs internos;
- operator audit mascara IDs por padrão;
- reveal exige papel `operator` explícito;
- worker logs não imprimem mensagens, tokens, JIDs ou auth state.

Nickname/display name é apresentação contextual. Nunca substitui ID estável como identidade forte.

## 8. Worker credential

Workers externos usam `AMORA_MESSAGING_SERVICE_TOKEN`.

Esse token:

- é distinto do admin;
- é distinto do voice service token;
- em cloud exige pelo menos 32 bytes;
- acessa somente `GET/POST /v1/messaging/worker/*`;
- não acessa `/v1/state`, Memory, World, Actions, Pessoas, Console ou operator APIs.

Comprometer um worker não deve equivaler a comprometer o Core administrativo.

## 9. PresenceController

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

Ao reiniciar o Core, qualquer estado físico anterior é resetado para `DISCONNECTED` com `CORE_RESTART_TRANSPORT_UNKNOWN`, mas `desired_connected` permanece. Assim o Core nunca finge que um socket sobreviveu à troca de processo/compute.

Um `CHECK` pode fazer connect/check/disconnect bounded quando `desired_connected=false`.

## 10. Discord

Identidade forte: Discord user ID.

Adapter atual usa Gateway v10 + REST v10 e preserva:

- author user ID;
- guild/channel/message IDs;
- native mentions;
- message reference/reply;
- attachment metadata.

Para texto normal em guilds, o bot precisa do `MESSAGE_CONTENT` intent habilitado quando exigido pelo Discord. Nickname/global display name é somente apresentação/alias contextual.

Mensagens de bots/self são descartadas pelo worker.

## 11. WhatsApp linked-device

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

O worker atual usa `useMultiFileAuthState` para bootstrap/staging. A própria documentação do Baileys não recomenda esse helper como armazenamento de produção. Antes de declarar deploy definitivo, migrar auth para backend robusto/durável e provar reconnect/sync com conta real.

## 12. Status físico

Implementação do contrato/Core/workers não equivale a prova de conectividade real.

Ainda exigem staging físico:

- bot/token Discord real e intents/permissões do servidor;
- conta WhatsApp real, linked-device pairing e reconnect/history behavior;
- armazenamento produtivo do auth state WhatsApp;
- limites/retry/ban behavior reais.

Até esse gate passar, o código é `IMPLEMENTED / STAGING PENDING`, não “conectado em produção”.
