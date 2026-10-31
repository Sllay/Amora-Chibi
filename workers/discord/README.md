# Amora Discord Worker

Worker isolado que traduz Discord Gateway/REST para o contrato comum `PlatformMessagingContract` do Core.

## Estado

`IMPLEMENTED / STAGING PENDING`.

Os contratos e testes locais estão implementados; não há alegação de conectividade real até testar com bot/token/guild reais.

## Variáveis

```text
AMORA_CORE_URL=https://core.example
AMORA_MESSAGING_SERVICE_TOKEN=<secret distinto do admin>
AMORA_PLATFORM_ACCOUNT_SCOPE=primary
DISCORD_BOT_TOKEN=<bot token>
AMORA_PLATFORM_REMOTE_PROCESSING=false
```

## Discord Developer Portal

O worker solicita:

- `GUILDS`;
- `GUILD_MESSAGES`;
- `DIRECT_MESSAGES`;
- `MESSAGE_CONTENT`.

Para o comportamento textual pretendido em guilds, habilite `MESSAGE_CONTENT` quando o Discord exigir o privileged intent para o app.

Permissões mínimas do bot devem cobrir somente os canais usados e, conforme o caso, View Channel / Read Message History / Send Messages. Evite permissões administrativas.

## Identidade e replies

- identidade forte: Discord user ID;
- nickname/global display name: somente apresentação contextual;
- native reply/message reference é preservado;
- outbound usa `allowed_mentions.parse=[]`, portanto texto gerado não pinga `@everyone`, roles ou usuários por acidente;
- mensagens de bots/self são descartadas.

## Presence

O worker não decide ficar online. Ele lê o estado desejado do `PlatformPresenceController` no Core e reporta o transporte físico.

`desired_connected=false + check_requested=true` permite um check bounded: conecta, sincroniza o necessário, reporta check e desconecta.

## Segurança

- worker usa somente `AMORA_MESSAGING_SERVICE_TOKEN`;
- esse token não acessa Memory, World, Actions, People Settings ou Console;
- logs não imprimem conteúdo de mensagem, token ou user ID;
- REST usa API v10 e redirects recusados;
- requests têm timeout;
- reconnect/heartbeat/Resume são tratados no processo isolado.
