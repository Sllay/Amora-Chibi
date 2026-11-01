# Northflank staging — Discord worker

O Discord worker roda como serviço separado do Amora Core.

## Build

Crie um novo serviço no mesmo projeto Northflank, usando o mesmo repositório/branch e Dockerfile customizado:

```text
workers/discord/Dockerfile
```

O worker não precisa expor porta pública. Ele abre conexão de saída com o Discord Gateway/REST e conversa com o Core via HTTPS.

## Runtime variables

Configure estas variáveis no serviço Discord:

```env
AMORA_CORE_URL=https://<url-publica-do-core>
AMORA_MESSAGING_SERVICE_TOKEN=
AMORA_PLATFORM_ACCOUNT_SCOPE=primary
AMORA_PLATFORM_REMOTE_PROCESSING=false
DISCORD_APPLICATION_ID=
DISCORD_TEST_GUILD_ID=
DISCORD_BOT_TOKEN=
```

`DISCORD_BOT_TOKEN` é segredo do Discord e nunca deve entrar no Git/logs.

`AMORA_MESSAGING_SERVICE_TOKEN` é um segredo interno compartilhado entre Core e workers. O mesmo valor deve existir no serviço Core e no serviço Discord; em cloud precisa ter pelo menos 32 bytes e deve ser diferente do admin token e do voice service token.

`DISCORD_APPLICATION_ID` e `DISCORD_TEST_GUILD_ID` não são tokens de autenticação. Ainda assim, mantenha configuração específica do deploy no Northflank em vez de codificá-la no runtime.

A Discord Application Public Key não é usada por este worker porque Application Commands são recebidos pelo Gateway (`INTERACTION_CREATE`) e respondidos pelos endpoints de interação do Discord. O permission integer usado no invite também não é variável de runtime.

## Primeiro boot

1. Faça deploy do Core com `AMORA_MESSAGING_SERVICE_TOKEN` configurado.
2. Faça deploy do serviço Discord com o mesmo `AMORA_MESSAGING_SERVICE_TOKEN` e as variáveis Discord.
3. No Core/operator solicite `desired_connected=true` para Discord.
4. Confirme nos logs do worker eventos seguros `START`, `READY` e, quando `DISCORD_TEST_GUILD_ID` estiver configurado, `STAGING_COMMAND_READY`.
5. Confirme no Discord que o bot fica online e execute a matriz física de staging em `workers/discord/README.md` / issue de certificação.

Nunca copie tokens reais para issues, PRs, arquivos `.env` versionados ou mensagens de chat.
