# AMORA — People Identity / Living Profiles

**Status:** CANÔNICO / EM IMPLEMENTAÇÃO  
**Atualizado em:** 2026-10-31

## 1. Princípio

Uma pessoa canônica não é um número de telefone, nickname, conta, dispositivo ou sessão.

```text
PERSON
├── profile bootstrap estável
├── aliases
├── Relationship Graph
├── World/Memory sobre a pessoa
├── directed affect da Amora
└── zero ou mais PlatformIdentityBindings
```

`PERSON != DEVICE != SESSION != PLATFORM ACCOUNT` permanece invariante.

## 2. Profiles não são uma segunda memória

`config/people_profiles.json` existe para bootstrap/operator-owned data pequena e estável:

- entity id canônico;
- nome canônico;
- aliases iniciais;
- nascimento ou idade quando BK quiser preencher;
- descrição curta opcional;
- autoridade estrutural explícita;
- relações estruturais seedadas.

Preferências aprendidas, episódios, fatos observados, crenças, confiança, afeto e histórico continuam pertencendo a World Model, Memory, Relationship Graph e Emotion Engine.

A visão futura de um "perfil vivo" deve ser uma projeção dessas fontes, não um JSON gigante que o provider reescreve.

## 3. Perfis seed atuais

- `PERSON_BK` — pai da Amora; também pai de Lucas; única autoridade inicial que pode definir parentesco parental estrutural da Amora;
- `PERSON_LUCAS` — filho de BK e irmão da Amora;
- `PERSON_NICOLAS` — primo da Amora.

Relações não declaradas explicitamente não são inferidas a partir de frases coloquiais soltas.

`ENTITY_CHATGPT mother_of ENTITY_AMORA` continua parte da identidade canônica já existente.

## 4. Autoridade de família

Texto de terceiros nunca substitui a estrutura familiar canônica.

Para `father_of` / `mother_of` direcionado à Amora, ou `child_of` onde Amora é a criança:

```text
fala de desconhecido/familiar sem autoridade
→ pode existir como fala/hipótese contextual
→ NÃO promove Relationship Graph

fala de PERSON_BK
→ é elegível ao caminho autoritativo/local definido pelo Core
```

A repetição de "sou sua mãe" não transforma um desconhecido em mãe da Amora.

## 5. PlatformIdentityBinding

Uma pessoa pode possuir múltiplas identidades externas simultaneamente.

Exemplos válidos:

```text
PERSON_BK
├── whatsapp phone_e164 A
├── whatsapp phone_e164 B
├── whatsapp lid C
├── discord user_id 1
├── discord user_id 2
└── web authenticated session
```

O identificador externo é dado privado de runtime. Ele não deve aparecer em prompt, health report, log humano normal ou no catálogo público de profiles.

O Core recebe do adapter um actor id já extraído da plataforma e resolve:

```text
(platform, identifier_type, account_scope, external_actor_id)
→ PlatformIdentityBinding
→ PERSON_*
```

Nickname/display name pode virar alias candidato, mas nunca é identidade forte por si só.

## 6. Configuração privada de bindings

O arquivo padrão é:

```text
~/.amora/secrets/identity_bindings.json
```

Pode ser substituído por `AMORA_IDENTITY_BINDINGS_FILE`.

Existe apenas um exemplo vazio versionado em `config/identity_bindings.example.json`. Números reais e Discord IDs não devem ser commitados.

Formato:

```json
{
  "schema_version": 1,
  "bindings": [
    {
      "platform": "whatsapp",
      "identifier_type": "phone_e164",
      "external_actor_id": "<numero sem expor no git>",
      "entity_id": "PERSON_BK",
      "account_scope": "amora-primary",
      "trust": "operator_verified",
      "confidence": 1.0,
      "enabled": true
    }
  ]
}
```

Ao iniciar, bindings do arquivo privado são importados/upsertados no banco canônico e sobrevivem restart. O arquivo pode então funcionar apenas como bootstrap/override privado.

## 7. Política de mensageria aprovada para adapters futuros

### Grupo

Participantes do grupo podem contribuir para contexto social. Se alguém falar diretamente com Amora por mention, reply/quote, vocativo ou inferência social forte, o Social Attention Router poderá decidir responder.

Pessoa desconhecida recebe confiança/relational attitude menor que familiar conhecido; isso não significa transformar desconhecido automaticamente em entidade familiar.

### DM / mensagem privada

Por decisão de produto atual, DM de desconhecido não entra na conversa cognitiva normal. Somente pessoas conhecidas/autorizadas poderão obter interação privada.

Adapters devem aplicar o gate de identidade antes de entregar a mensagem ao Cognitive Core.

### Histórico e auditoria

Mensageria integrada deve persistir IDs de mensagem, direção, speaker canônico quando resolvido, conversation id, reply/mention metadata, timestamps e resultado de roteamento suficientes para auditoria e segurança.

Isso é histórico operacional/social; não significa consolidar todo texto automaticamente como memória de longo prazo.

## 8. Presença por plataforma

WhatsApp e Discord não precisam ficar conectados permanentemente.

O design alvo inclui `PlatformPresenceController`:

```text
DISCONNECTED
→ CONNECTING
→ CONNECTED
→ IDLE/CHECKING
→ DISCONNECTING
```

Ação explícita da Amora/BK pode conectar ou desconectar uma plataforma. Scheduler/autonomia poderá solicitar checagens periódicas bounded.

Enquanto um adapter estiver realmente desconectado, o Core não deve fingir que sabe imediatamente que chegou uma mensagem; descoberta só ocorre quando a plataforma reconectar/sincronizar ou quando houver um canal oficial de webhook/push independente.

## 9. Web

A Web pareada atual é uma superfície privada. Enquanto o produto mantiver a regra atual de acesso, uma sessão Web autenticada/pareada representa `PERSON_BK`; não inferir o speaker a partir do texto digitado.

O futuro Console Web é outra view da mesma superfície autenticada, não outra identidade nem outro Core.
