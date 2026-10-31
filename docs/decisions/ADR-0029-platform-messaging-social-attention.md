# ADR-0029 — Platform Messaging, Social Attention e Worker Boundary

**Status:** accepted  
**Data:** 2026-10-31

## Contexto

Amora precisa existir em Web, Discord, WhatsApp e futuras superfícies sem virar uma personalidade separada em cada plataforma. O Core já possui identidade canônica, People Profiles e PlatformIdentityBindings. Faltava uma fronteira de mensageria que resolvesse quatro riscos:

1. plataforma decidir identidade/cognição;
2. desconhecido em DM ganhar acesso ao Core só por enviar texto;
3. grupos fazerem Amora responder indefinidamente a qualquer mensagem;
4. worker externo receber privilégio administrativo amplo.

Também era necessário separar histórico de segurança de memória de longo prazo.

## Decisão

Adotar uma camada `PlatformMessagingContract` independente do SDK/transport.

```text
Discord/Baileys worker
→ InboundMessage
→ binding resolution
→ SocialAttentionRouter / ConversationFloor
→ cognition
→ OutboundMessage queue
→ worker
```

### Identidade

O adapter fornece IDs externos estáveis; o Core resolve PERSON por `PlatformIdentityBinding`. Display name/nickname não é autenticação nem identidade forte.

Pessoa desconhecida de grupo pode receber apenas um pseudônimo efêmero `EXT_*`; isso não cria `PERSON_*` nem relação canônica.

### DM

DM desconhecida é quarantined antes de CognitiveStore/provider. Pode ser conservada no audit privado para BK investigar manipulação, spam ou abuso.

### Grupo

Mensagens ambiente podem virar contexto recente, mas não turno. Native reply/quote e @mention são evidências mais fortes que heurísticas de texto. O floor é bounded e generation-based para cancelar resposta que ficou socialmente velha enquanto o provider gerava.

### Memória

Messaging Audit é store operacional próprio. Mensageria não consolida automaticamente em Memory/Relationship Graph.

### Worker security

Workers usam credencial própria `messaging`, limitada a `/v1/messaging/worker/*`. Admin/operator/web/avatar não são delegados ao processo de transporte.

### Presence

Persistir intenção (`desired_connected`, `check_requested`) e não fingir persistência de socket. Restart reseta transporte físico para disconnected.

### Discord

Usar Discord user ID, Gateway/REST e primitiva nativa de reply. MESSAGE_CONTENT é requisito de configuração quando aplicável.

### WhatsApp

Worker linked-device fica fora do Core. Baileys é explicitamente integração não-oficial, com release direta pinada e hardening contra history/upsert spoof. Auth state é secret longa; helper multi-file é staging/bootstrap, não armazenamento produtivo final.

## Alternativas rejeitadas

### Adapter chamar Cognitive Core diretamente com texto cru

Rejeitado porque mistura SDK/transport, identidade, policy social e cognition; também permitiria que plataforma atribuísse speaker/addressee.

### Entregar DM desconhecida ao Core e decidir silêncio depois

Rejeitado porque o texto já teria contaminado CognitiveStore/context/provider antes do gate de autorização.

### Tratar toda mensagem de grupo como dirigida à Amora

Rejeitado porque produz comportamento intrusivo e floor infinito.

### Renovar o floor em toda continuidade implícita

Rejeitado porque uma única menção poderia manter Amora respondendo indefinidamente.

### Nickname/display name como pessoa

Rejeitado por ser mutável, colidível e controlado pelo usuário/plataforma.

### Worker receber admin token

Rejeitado porque uma falha no bot/SDK viraria acesso completo a Memory, World, Actions e configuração.

### Audit bruto alimentar Memory automaticamente

Rejeitado porque grupos/DMs contêm ruído, manipulação e terceiros sem consentimento; audit e lembrança têm propósitos diferentes.

### Persistir `CONNECTED` após restart

Rejeitado porque estado lógico no banco não prova que o socket físico sobreviveu.

## Consequências

Positivas:

- uma identidade/cognição única em todas as superfícies;
- DM desconhecida bloqueada antes do cérebro;
- grupos socialmente menos invasivos;
- reply nativo reduz ambiguidade;
- transporte comprometido tem privilégio mínimo;
- histórico auditável sem transformar conversa alheia em memória.

Custos:

- workers precisam traduzir payloads cuidadosamente;
- presença real exige coordenação Core ↔ worker;
- WhatsApp linked-device continua dependente de integração não-oficial e staging físico;
- alias learning e promoção de desconhecidos para PERSON ficam para policy posterior.

## Gate de aceitação

- schema 13 migra local e libSQL;
- unknown DM não cria CognitiveMessage;
- known binding resolve PERSON correta;
- ambient group não responde;
- native reply/mention pode responder;
- floor shift invalida reply antigo;
- worker token não acessa admin/operator;
- audit privado não aparece em health/log;
- Discord/WhatsApp adapter contract tests passam;
- Core Python 3.11–3.14, security e container gates verdes.

Conectividade real de Discord/WhatsApp é gate de staging separado e não pode ser inferida de testes locais.
