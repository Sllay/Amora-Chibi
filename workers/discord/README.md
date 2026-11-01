# Amora Discord Worker

Worker isolado que traduz Discord Gateway/REST para o contrato comum `PlatformMessagingContract` do Core.

## Estado

`TEXT SOCIAL RUNTIME IMPLEMENTED / REAL BOT STAGING PENDING`.

O runtime textual, contratos e testes automatizados estão implementados. **Ainda não alegamos conectividade real** até criar uma Discord Application/Bot, instalar em uma guild de teste, fornecer token real e executar a matriz física abaixo.

Discord Voice/Call é outro subsistema e não pertence a este worker textual.

## Arquitetura

```text
Discord Gateway / REST
        │
        ▼
DiscordWorker
├── Gateway session + heartbeat + Resume
├── bounded ChannelSession cache
├── MESSAGE_CREATE/UPDATE/DELETE/BULK_DELETE
├── reaction transport evidence
├── guild /amora staging fallback
├── DiscordRestClient / rate-limit controller
└── stale-reply validation
        │
        ▼
Messaging worker credential
        │
        ▼
Amora Core
├── PlatformIdentityBinding
├── SocialAttentionRouter
├── ConversationFloor
├── platform presence authority
├── Cognitive Core (quando necessário)
└── outbound queue
```

O worker é olhos/boca/transporte. Ele **não** decide pessoa canônica, memória, emoção, parentesco, autoridade nem se Amora deve tomar o turno.

## Variáveis

```text
AMORA_CORE_URL=https://core.example
AMORA_MESSAGING_SERVICE_TOKEN=<secret distinto do admin/operator>
AMORA_PLATFORM_ACCOUNT_SCOPE=primary
DISCORD_BOT_TOKEN=<bot token privado>
DISCORD_APPLICATION_ID=<application id; recomendado no staging>
DISCORD_TEST_GUILD_ID=<guild id para registrar /amora; staging somente>
AMORA_PLATFORM_REMOTE_PROCESSING=false
```

Nunca colocar `DISCORD_BOT_TOKEN` no Git, log, screenshot público ou arquivo versionado. Interaction tokens também são tratados como secretos transitórios e nunca entram em logs normais.

## Criar o bot de staging

1. No Discord Developer Portal, criar uma **Application** dedicada à Amora.
2. Criar/habilitar o **Bot User** dessa Application.
3. Na página Bot, habilitar **Message Content Intent** para o teste textual.
4. Não habilitar Guild Members ou Presence apenas por conveniência; o runtime textual atual não precisa deles.
5. Gerar o install/invite da Application para uma guild de teste.
6. Conceder somente permissões necessárias nos canais usados; evitar `Administrator`.
7. Guardar o bot token como secret `DISCORD_BOT_TOKEN` no ambiente do worker.
8. Informar `DISCORD_APPLICATION_ID` e `DISCORD_TEST_GUILD_ID` para o worker registrar o comando guild `/amora` de staging.
9. Cadastrar o Discord user ID de BK/Lucas/etc. em `PlatformIdentityBinding` pela Web Pessoas/operador.
10. Para uma pessoa poder ordenar connect/disconnect/check/status, o profile precisa de `can_control_platform_presence=true`.
11. Solicitar `desired_connected=true` pela Web/operator para a primeira conexão física.

Em 2026 o Discord classifica Message Content como privileged intent. Apps acessíveis a menos de 10.000 usuários podem habilitá-lo no Developer Portal sem processo de revisão; acima do limiar vigente, o Discord exige revisão/renovação conforme a política atual. Revalidar a documentação antes de qualquer expansão pública.

Guild application commands são usados só no staging porque aparecem imediatamente na guild de teste. O worker registra/atualiza `/amora` uma vez por processo após `READY`; comando global fica para eventual uso público posterior.

## Gateway intents usados

O worker solicita apenas:

- `GUILDS`;
- `GUILD_MESSAGES`;
- `GUILD_MESSAGE_REACTIONS`;
- `DIRECT_MESSAGES`;
- `DIRECT_MESSAGE_REACTIONS`;
- `MESSAGE_CONTENT`.

Ele não solicita Guild Members nem Guild Presences.

## Permissões de canal

Para o primeiro staging, conceder somente o que a guild/canal exigir para:

- visualizar o canal;
- ler histórico quando necessário para resolver reply references;
- enviar mensagens;
- responder em threads/canais onde o teste ocorrer;
- ler/adicionar reações apenas se a matriz de reação for testada.

`Administrator` não é requisito e não deve ser usado como atalho de staging.

## Identidade

```text
Discord user ID
→ PlatformIdentityBinding
→ PERSON_*
```

Nickname, username e global display name são apenas apresentação/alias contextual. Nunca substituem o stable user ID como identidade forte.

Mensagens de bot/self não viram fala humana e não entram na cognição normal.

## Sessão social textual

O worker mantém cache bounded por canal para conhecer a revisão nativa de mensagens recentes. Isso não é Memory da Amora.

### Create

```text
MESSAGE_CREATE
→ record revision 1
→ normalize
→ Core social gate/cognition
→ outbound vinculado ao native message id
```

### Edit

```text
MESSAGE_UPDATE
→ merge do payload parcial com cache
→ Core invalida floor/outbound da versão antiga
→ se content mudou:
   synthetic delivery id = <native>#edit:<revision>
   native_message_id permanece o mesmo
   nova revisão pode ser processada
```

Assim uma edição não é deduplicada como se fosse a mesma entrega, mas replies continuam apontando para o objeto Discord correto.

### Delete

```text
MESSAGE_DELETE / MESSAGE_DELETE_BULK
→ marcar source como deleted
→ invalidar ConversationFloor
→ cancelar outbound queued/claimed ligado à source
→ worker faz uma segunda validação antes do send
```

Uma resposta pronta para uma mensagem apagada/revisada não deve sair só porque o provider terminou depois.

### Reactions

Reaction add/remove fica como evidência de transporte na sessão bounded. O worker **não fabrica** `"fulano reagiu com ❤️"` como se fosse utterance humana e não alimenta o provider artificialmente.

## Native replies e mentions

- reply/message reference nativo é preservado;
- outbound usa `allowed_mentions.parse=[]` e `replied_user=false`;
- texto gerado não pode pingar `@everyone`, roles ou usuários por acidente;
- futura mention explícita deve ser uma decisão estruturada do Core, não texto cru do provider.

## Controle de presença em linguagem natural

O parser de controle vive no Core, não no worker.

Exemplos explícitos reconhecidos:

```text
Amora, conecta no Discord
Amora, desconecta do Discord
Amora, checa o Discord
status do Discord
```

Fluxo:

```text
stable Discord actor id
→ PlatformIdentityBinding
→ canonical PERSON
→ can_control_platform_presence?
   ├─ sim → PlatformPresenceController
   └─ não → nenhuma mutação
```

Palavras de voice/call (`call`, `chamada`, `voz`, `voice`, `canal de voz`) são excluídas desse parser para não confundir transporte textual com a futura presença de voz.

O controle determinístico não passa pelo provider e não vira Memory automaticamente.

### Limite físico de connect vindo do próprio Discord

Se o Gateway Discord estiver **realmente desconectado**, nenhuma mensagem ou slash command enviado dentro do Discord chega ao worker. Portanto:

```text
worker Discord já conectado
→ mensagem natural ou /amora pode status/check/disconnect/connect-idempotente

worker Discord totalmente offline
→ wake/connect deve vir de Web/operator ou outra superfície ainda conectada
```

O sistema não finge conhecer comandos recebidos enquanto o transporte estava offline.

## `/amora` — fallback determinístico de staging

Com `DISCORD_APPLICATION_ID` + `DISCORD_TEST_GUILD_ID`, o worker registra um guild command:

```text
/amora acao: Status
/amora acao: Conectar
/amora acao: Desconectar
/amora acao: Checar
```

Esse comando **não** concede autoridade por role Discord. Fluxo:

```text
INTERACTION_CREATE
→ worker ACK/defer efêmero imediatamente
→ Discord user ID do invocador
→ POST /v1/messaging/worker/presence-control
→ PlatformIdentityBinding
→ PERSON_*
→ can_control_platform_presence?
→ resultado local
→ editar resposta efêmera
```

O defer inicial existe porque Gateway interactions precisam de resposta HTTP inicial dentro do deadline do Discord. O callback inicial usa uma única tentativa curta; a decisão de autorização não depende do Discord.

### Instalação existente

O catálogo de pessoas é bootstrap-only. Portanto um banco já existente não recebe capability nova silenciosamente só porque o seed mudou.

O operador pode conceder/revogar explicitamente:

```http
PUT /v1/operator/messaging/presence-authority/PERSON_BK
{
  "enabled": true,
  "confirm": true
}
```

Em instalação nova, o seed inicial concede essa capability a `PERSON_BK` e não a Lucas/Nicolas.

## Presence de transporte

O worker não decide ficar online sozinho. Ele lê o estado desejado do `PlatformPresenceController` no Core e reporta estado físico.

```text
DISCONNECTED
→ CONNECTING
→ CONNECTED
→ CHECKING
→ DISCONNECTING
```

`desired_connected=false + check_requested=true` permite check bounded.

Core restart nunca presume que o socket antigo sobreviveu.

## Gateway recovery

- heartbeat e heartbeat ACK são acompanhados;
- ACK perdido força reconnect;
- Session ID + sequence são preservados para Resume quando permitido;
- close `4007`/`4009` limpa estado de Resume;
- close terminal de configuração/autorização (`4004`, `4010`, `4011`, `4013`, `4014`) entra em estado de erro e **não** cria reconnect storm;
- reconnect não inventa mensagens que o worker não recebeu.

## REST / rate limit

`DiscordRestClient` possui retry bounded:

- `429`: respeita `retry_after` / headers de rate limit;
- `5xx`: backoff exponencial bounded;
- timeout/network: retry bounded;
- `401/403/404`: terminal, sem hot loop;
- callback inicial de interaction pode reduzir `maxAttempts` por deadline.

O código nunca tenta contornar rate limits do Discord.

## Matriz física mínima antes de chamar ONLINE

### Conexão

- [ ] Application/Bot real criado;
- [ ] Message Content habilitado;
- [ ] bot instalado sem Administrator;
- [ ] `DISCORD_APPLICATION_ID` e `DISCORD_TEST_GUILD_ID` configurados;
- [ ] `/amora` aparece na guild de staging;
- [ ] worker READY;
- [ ] presence do Core chega a CONNECTED;
- [ ] restart do worker faz Resume ou nova sessão válida;
- [ ] token inválido/intent inválido degrada sem loop infinito.

### Texto social

- [ ] `@Amora oi` responde;
- [ ] reply nativo a uma mensagem da Amora responde sem nova mention;
- [ ] conversa ambiente sem chamar Amora não força resposta;
- [ ] outro speaker toma o floor e resposta velha é suprimida;
- [ ] mensagem editada durante geração cancela a revisão antiga;
- [ ] mensagem apagada durante geração cancela outbound;
- [ ] self/bot echo não cria loop;
- [ ] mensagem longa é dividida <= 2000 chars;
- [ ] `@everyone` produzido em texto não gera ping real;
- [ ] falta de permissão 403 degrada sem retry storm;
- [ ] 429 é respeitado.

### Identidade/privacidade

- [ ] BK resolve por Discord user ID;
- [ ] Lucas/Nicolas resolvem por seus próprios IDs quando cadastrados;
- [ ] unknown DM é barrada antes da cognição;
- [ ] unknown em grupo não vira familiar automaticamente;
- [ ] logs não exibem conteúdo, bot token, interaction token ou user IDs crus.

### Controle

- [ ] BK autorizado: `conecta no Discord` altera desired presence enquanto o worker está acessível;
- [ ] `/amora status` resolve BK pela binding e retorna resposta efêmera;
- [ ] `/amora desconectar` funciona e o worker encerra transporte depois da resposta;
- [ ] pessoa conhecida sem capability não altera;
- [ ] desconhecido não altera;
- [ ] worker credential não consegue conceder capability;
- [ ] `entra na call do Discord` **não** é confundido com connect textual.

Somente depois desta matriz passar com rede real o status muda de `STAGING PENDING` para `LIVE CERTIFIED`.

## Fora de escopo: Discord Voice

Call/voz exige subsistema próprio (Voice Gateway, transporte de mídia, speaker streams, Opus e requisitos atuais de criptografia do Discord). Não adicionar áudio improvisado neste worker textual.
