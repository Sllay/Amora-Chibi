# ADR-0030 — Discord Social Text Runtime antes de Discord Voice

**Status:** accepted  
**Data:** 2026-10-31

## Contexto

ADR-0029 criou a fronteira comum de mensageria, SocialAttentionRouter, ConversationFloor, audit, outbound queue, presence e worker isolado. O primeiro Discord worker provava o contrato básico, mas ainda era insuficiente para o produto desejado por BK: Amora precisa permanecer socialmente coerente quando mensagens são editadas/apagadas, o assunto muda enquanto ela pensa, a conexão é retomada, há rate limit ou uma permissão desaparece.

Um relay `MESSAGE_CREATE → Core → POST message` pode funcionar no happy path e falhar exatamente nos casos que tornam uma presença social convincente.

Também surgiu uma segunda fronteira de autoridade: comandos como `Amora, conecta no Discord` não podem ser decididos por regex no worker nem autorizados por cargo/nickname do Discord. Stable platform identity precisa resolver uma PERSON canônica e a capability precisa pertencer ao Core.

Discord Voice/Call possui ciclo de vida e transporte próprios e não deve ser misturado ao hardening textual.

## Decisão

Criar `DiscordSocialRuntime` bounded dentro do worker textual e manter toda autoridade no Core.

```text
Discord native events
        │
        ▼
DiscordSocialRuntime
(message revision / deletion / reaction evidence)
        │
        ├── InboundMessage revision
        └── TransportInvalidation
                    │
                    ▼
Amora Core
├── PlatformIdentityBinding
├── SocialAttentionRouter / ConversationFloor
├── presence-control authority
├── cognition quando necessário
└── outbound queue
                    │
                    ▼
Discord worker validates native source again
                    │
                    ▼
Discord REST
```

### Mensagem editada

`MESSAGE_UPDATE` é parcial. O worker combina o payload com cache bounded recente e incrementa `transport_revision`.

O Core recebe primeiro uma `TransportInvalidation` da source nativa para revogar floor/outbound antigo. Quando `content` realmente mudou, a revisão nova entra com:

```text
message_id        = <native>#edit:<revision>   # delivery/dedupe
native_message_id = <native Discord id>        # reply/source
transport_revision = N
```

Isso mantém dedupe de entregas sem quebrar native reply.

### Mensagem apagada

`MESSAGE_DELETE`/`MESSAGE_DELETE_BULK`:

1. marca a source como deleted no cache bounded;
2. invalida generation do ConversationFloor no Core;
3. cancela outbound queued/claimed por native source;
4. worker valida novamente imediatamente antes do send.

A proteção é deliberadamente em duas camadas porque update/delete pode ocorrer durante geração ou depois de o Core já ter enfileirado a resposta.

### Reactions

Reactions são evidência de transporte/social bounded. Não são convertidas automaticamente em texto humano nem enviadas ao provider como utterance inventada.

### Rate limits e erros REST

`DiscordRestClient` aplica retry bounded:

- `429`: respeita `retry_after`/headers;
- `5xx`: backoff bounded;
- timeout/network: retry bounded;
- `401/403/404`: terminal para a operação.

Nunca contornar rate limit por loops paralelos ou retry agressivo.

### Gateway recovery

Preservar session id + sequence para Resume quando válido. Close codes que tornam a sessão não-resumível limpam resume state. Close codes terminais de auth/config/intents/sharding entram em estado de erro e interrompem reconnect storm.

Reconnect nunca prova que mensagens do intervalo foram recebidas; Amora não pode fingir ter visto algo que o transporte perdeu.

### Autoridade de presença

Adicionar ao `PersonProfileSeed`:

```text
can_control_platform_presence: bool
```

É capability distinta de `can_define_amora_family`.

Fluxo de mutação:

```text
external Discord user ID
→ PlatformIdentityBinding
→ PERSON_*
→ can_control_platform_presence?
→ PlatformPresenceController
```

O worker nunca recebe poder para escolher autoridade com base em Discord role, nickname ou guild admin.

Instalações novas seedam `PERSON_BK=true`; perfis persistidos existentes continuam vencendo o seed. Operador pode conceder/revogar explicitamente via endpoint operator confirmado.

### Linguagem natural

Comandos explícitos de presença são reconhecidos no Core antes do provider, por gramática bounded. Exemplos:

- conecta no Discord;
- desconecta do Discord;
- checa o Discord;
- status do Discord.

Frases ambíguas seguem para cognição normal. Termos de call/voz são excluídos do parser textual para não executar a operação errada.

Unknown DM continua bloqueada antes desse parser.

### Discord Voice

Discord Voice/Call permanece subsistema separado. Não adicionar Voice Gateway, UDP, Opus, DAVE ou STT/TTS a este worker textual.

## Alternativas rejeitadas

### Tratar edit como novo MESSAGE_CREATE independente

Rejeitado porque perderia relação com a source nativa e poderia enviar duas respostas contraditórias para o mesmo objeto Discord.

### Ignorar delete depois que a geração começou

Rejeitado porque Amora poderia responder a conteúdo que já não existe.

### Guardar histórico Discord ilimitado no worker

Rejeitado porque duplica Memory/audit, aumenta risco de privacidade e não é necessário para stale-reply validation.

### Reaction virar texto sintético

Rejeitado porque fabrica utterance e pode distorcer cognição/relacionamento.

### Retry infinito em 429/403/close terminal

Rejeitado porque pode causar hot loop, amplificar outage e violar limites de plataforma.

### Discord admin/role autorizar presença

Rejeitado porque autoridade canônica da Amora não pertence à guild e cargos Discord são mutáveis por terceiros.

### Reutilizar `can_define_amora_family`

Rejeitado porque autoridade familiar e controle operacional são capacidades semanticamente diferentes.

### Regex no worker executar connect/disconnect

Rejeitado porque o transporte não sabe quem é a pessoa canônica e não deve possuir policy de autorização.

### Implementar call no mesmo PR

Rejeitado porque texto e voz possuem lifecycle/failure modes distintos; voz precisa de arquitetura e staging próprios.

## Consequências

Positivas:

- edit/delete/context shift deixam de produzir respostas socialmente velhas;
- reconnect/config errors degradam sem tempestade;
- rate limit é tratado explicitamente;
- capability de presença é canônica e auditável;
- comando explícito não depende do provider;
- worker continua substituível e de privilégio mínimo.

Custos:

- mais estado bounded no worker;
- revisão de mensagem precisa de synthetic delivery id;
- instalação persistente existente precisa grant explícito da nova capability;
- Discord real ainda exige staging físico com Application/Bot/token/guild.

## Gate de aceitação

- JS contract tests de revision/delete/reaction/rate-limit passam;
- Python testa invalidation Core e cancelamento por native source;
- known authorized PERSON controla presence sem provider;
- known non-authorized PERSON não controla;
- unknown DM não chega ao controle;
- call/voice phrases não acionam connect textual;
- worker credential continua sem admin/operator access;
- Gateway terminal error não cria reconnect storm;
- Core Python 3.11–3.14, container, security e workers verdes no mesmo head;
- staging real é checklist separado e não é inferido por CI.

## Critério para revisitar

Revisar quando:

- Discord alterar Gateway event/intent/rate-limit contracts;
- a Amora ganhar Discord Voice Presence;
- reactions ganharem semântica estruturada no Core;
- houver necessidade de session state distribuído entre múltiplas réplicas do worker.
