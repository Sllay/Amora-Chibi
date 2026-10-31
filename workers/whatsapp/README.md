# Amora WhatsApp Linked-Device Worker

Worker isolado para a semântica desejada de uma conta/número normal da Amora participando de conversas/grupos como linked device.

## Estado

`IMPLEMENTED / STAGING PENDING / NÃO-OFICIAL`.

Baileys não é a Meta Cloud API. Existe risco operacional e de conta; nenhum teste local autoriza afirmar que o número real está conectado ou que a Meta suporta esse uso.

## Release e hardening

Dependência direta fixada:

```text
@whiskeysockets/baileys = 7.0.0-rc14
```

O CI instala a árvore, confirma a versão direta e roda `npm audit --omit=dev --audit-level=high`.

Defesa adicional ao advisory GHSA-qvv5-jq5g-4cgg / CVE-2026-48063:

- somente `messages.upsert` com `type=notify` vira input social;
- qualquer upsert com `requestId` é descartado;
- `syncFullHistory=false`;
- `shouldSyncHistoryMessage=()=>false`;
- eventos de history/append não são tratados como mensagens novas.

Mesmo com upstream corrigido, essas regras permanecem como defesa em profundidade.

## Auth state

`AMORA_WHATSAPP_AUTH_DIR` é obrigatório, absoluto, privado e durável.

Esse diretório contém material equivalente a sessão autenticada. Trate como segredo longo:

- nunca Git;
- nunca imagem Docker;
- nunca artifact público;
- nunca log;
- diretório 0700;
- pairing secret 0600;
- backup criptografado/controle de acesso no host de produção.

O worker atual usa `useMultiFileAuthState` para bootstrap/staging. A documentação do próprio Baileys avisa que esse helper não é armazenamento de produção recomendado. Antes do deploy definitivo, migrar para auth store robusto (SQL/NoSQL/secret volume transacional) e testar atomicidade/recovery.

## Pareamento

Variáveis:

```text
AMORA_CORE_URL=https://core.example
AMORA_MESSAGING_SERVICE_TOKEN=<secret distinto do admin>
AMORA_PLATFORM_ACCOUNT_SCOPE=primary
AMORA_WHATSAPP_AUTH_DIR=/durable/private/amora-whatsapp
AMORA_WHATSAPP_PHONE_NUMBER=<somente dígitos, opcional para pairing code>
AMORA_PLATFORM_REMOTE_PROCESSING=false
```

Quando ainda não registrado:

- com número configurado, tenta `requestPairingCode`;
- QR/pairing code não é impresso no stdout;
- dados temporários são escritos em `pairing-secret.txt` dentro do auth dir privado;
- após conexão aberta, o arquivo temporário é apagado.

## Identidade

O worker pode apresentar ao Core evidências estáveis:

- `whatsapp_jid`;
- `whatsapp_lid`;
- `phone_e164` quando o PN JID permite derivação segura.

O Core cruza essas evidências com `PlatformIdentityBinding`. Nenhum JID/número vira PERSON automaticamente.

## Reply / grupos

- group JID é conversation id, nunca pessoa;
- participant JID/LID/PN identifica o ator externo;
- mentions e quoted message são preservados no envelope normalizado;
- outbound tenta reply nativo usando a WAMessage original em cache bounded;
- depois de restart/delay, se a mensagem original não estiver no cache, envia texto sem quote em vez de reconstruir payload nativo inseguro.

## Disconnect

Desconectar presença usa `sock.end(undefined)`, preservando sessão linked-device. O worker não chama `logout()` para um simples disconnect.

Se o WhatsApp reportar logout/revogação, o Core recebe `WHATSAPP_LOGGED_OUT_REPAIR_REQUIRED` e o worker evita loop agressivo de reconnect até reparo.

## Gate antes de produção

Provar fisicamente com conta dedicada:

1. pairing inicial;
2. DM conhecida/desconhecida;
3. grupo comum;
4. native reply/mention;
5. reconnect após restart do worker;
6. offline sync sem reprocessar histórico como mensagem nova;
7. rotação/revogação de auth;
8. comportamento após horas/dias conectado e desconectado;
9. rate limits/bloqueios reais;
10. auth store produtivo substituindo o helper multi-file.
