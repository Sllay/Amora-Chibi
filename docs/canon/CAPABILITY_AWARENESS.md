# AMORA — Capability Awareness & Agentic Tool Use

**Documento:** contrato canônico de consciência operacional de capacidades  
**Status:** CANÔNICO / DIREÇÃO ARQUITETURAL  
**Atualizado em:** 2026-11-02  
**Escopo:** como a Amora descobre, entende, escolhe, combina, executa e acompanha capacidades externas sem virar um bot de palavras-chave.

---

## 1. Invariante central

> **Capacidade disponível não pode permanecer invisível para a cognição quando ela for relevante para decidir uma ação.**

A Amora não deve responder "não consigo" apenas porque o provider de linguagem não recebeu contexto sobre uma capacidade que o Core realmente possui.

Também não deve receber uma lista permanente e gigante de todos os módulos em todo turno.

A arquitetura correta é:

```text
módulos / adapters / plugins
        ↓
Capability Registry
        ↓
Capability Retriever
        ↓
subset relevante para o contexto atual
        ↓
Decision / Planner
        ↓
Policy / Authority / Consent
        ↓
Action Bus / Executor
        ↓
Observation
        ↓
continue / retry / replan / ask / defer / abort
```

O Core continua autoridade sobre identidade, estado, permissões, dados canônicos, IDs nativos, efeitos e persistência.

Providers podem ajudar a raciocinar ou realizar linguagem, mas **não inventam ferramentas, IDs físicos, permissões ou resultados**.

---

## 2. O que é uma Capability

Uma `Capability` é uma affordance acionável que a Amora pode considerar como parte de seu repertório operacional.

Exemplos futuros:

- `discord.expression.send`
- `discord.image.send`
- `discord.voice.join`
- `discord.member.move`
- `whatsapp.message.send`
- `screen.observe`
- `art.create`
- `minecraft.connect`
- `minecraft.move`
- `minecraft.interact`
- `speech.speak`
- `speech.stop`

Uma capability **não é** simplesmente um endpoint, função Python ou método de adapter.

Ela precisa possuir semântica suficiente para que o planejador saiba:

- o que ela faz;
- quando faz sentido;
- quais entradas aceita;
- qual resultado produz;
- pré-condições;
- efeitos esperados;
- riscos;
- custo;
- permissões;
- disponibilidade atual;
- se pode ser recusada socialmente;
- se exige confirmação/consentimento;
- como verificar sucesso;
- se pode ser repetida com segurança;
- como compor com outras capabilities.

---

## 3. Descriptor canônico

Conceitualmente, uma capability deve poder ser representada assim:

```yaml
capability_id: discord.expression.send
version: 1
semantic_action: express_visually
summary: >
  Envia uma expressão visual nativa em uma conversa do Discord,
  como uma figurinha conhecida e disponível.

inputs:
  conversation: ConversationRef
  expression: ExpressionRef

outputs:
  receipt: DeliveryReceipt

preconditions:
  - discord_connected
  - target_conversation_resolved
  - expression_known
  - expression_available
  - send_permission_available

policy:
  risk: low
  consent: implicit_for_normal_social_expression
  external_side_effect: true

social:
  may_refuse: true
  may_initiate: true
  interruption_priority: low

verification:
  expected_effect: message_delivered
  idempotency_required: true
```

O schema concreto pode evoluir, mas essas categorias são parte do contrato conceitual.

---

## 4. Capability Registry

O `Capability Registry` é a fonte local de verdade sobre **o que a Amora pode fazer agora**.

Ele agrega capacidades anunciadas por:

- plugins;
- adapters de plataforma;
- engines locais;
- módulos multimodais;
- serviços opcionais configurados;
- dispositivos conectados;
- runtimes futuros como Minecraft.

O Registry deve distinguir pelo menos:

```text
KNOWN_CAPABILITY
AVAILABLE_CAPABILITY
DEGRADED_CAPABILITY
UNAVAILABLE_CAPABILITY
FORBIDDEN_CAPABILITY
```

Uma capability pode existir no sistema mas estar indisponível por falta de conexão, permissão, credencial, recurso, dispositivo ou dependência.

A cognição não deve ser enganada por capacidades fisicamente presentes mas indisponíveis naquele momento.

---

## 5. Capability Retriever

A Amora **não recebe o catálogo completo em todo turno**.

O `Capability Retriever` seleciona um subconjunto pequeno e semanticamente relevante com base em:

- texto/ato atual;
- ConversationSession;
- objetivo/commitment ativo;
- entidades mencionadas;
- plataforma atual;
- contexto multimodal;
- estado social;
- resultado da ação anterior;
- capabilities já envolvidas no plano;
- disponibilidade;
- risco e permissões.

Exemplo:

```text
BK: "vem abrir esse sorrisão pro pai"

contexto social + repertório conhecido
        ↓
retriever considera:
  - discord.expression.send
  - speech.reply
  - silence

não considera:
  - minecraft.connect
  - screen.capture
  - file.export
```

O retriever fornece **affordances**, não comandos por substring.

---

## 6. Linguagem natural não é keyword gate

Frases naturais e implícitas podem resultar em ação quando o significado e o contexto sustentam isso.

Exemplos que podem convergir para a mesma capability:

```text
"manda uma figurinha engraçada"
"mete aquela doida aí"
"abre esse sorrisão pro pai"
"essa situação merece aquela figurinha"
```

Nenhuma dessas frases exige que exista um `if "figurinha"` como autoridade final.

Heurísticas de keyword podem existir apenas como:

- fallback conservador;
- compatibilidade;
- otimização de retrieval;
- defesa contra falso positivo.

Elas não definem a semântica canônica da ação.

---

## 7. Agência social: poder fazer ≠ dever fazer

Uma capability disponível não obriga a Amora a executá-la.

A decisão deve considerar quando aplicável:

- relação;
- autoridade;
- contexto social;
- mood;
- directed affect;
- irritation;
- playfulness;
- urgência;
- gravidade;
- custo;
- privacidade;
- risco;
- regras de segurança;
- intenção atual;
- interrupções;
- oportunidade.

Pedidos triviais/brincalhões podem legitimamente resultar em:

- executar;
- zoar antes de executar;
- recusar socialmente;
- ignorar;
- trocar por outra expressão;
- ficar em silêncio.

Pedidos sérios e autorizados devem ser tratados com prioridade compatível com sua gravidade.

**Insistência social não contorna permissões, consentimento, risco ou segurança.**

---

## 8. Composição entre módulos

Capabilities devem poder compor resultados tipados.

Exemplo:

```text
BK: "vê minha tela, faz um desenho desse estilo e manda pra mim no Discord"

screen.observe
      ↓ VisualReference
art.create
      ↓ ImageArtifact
delivery.resolve_target
      ↓ DiscordConversationRef
discord.image.send
      ↓ DeliveryReceipt
```

Nenhum módulo precisa conhecer internamente todos os demais.

O Planner conecta capabilities por tipos de entrada/saída e objetivo.

"manda pra mim" pode ser resolvido por contexto relacional e bindings ativos; "manda no Discord no canal mídias" adiciona restrição explícita de transporte/destino.

Se o destino não puder ser resolvido com confiança suficiente, a ação pode entrar em `WAITING_FOR_USER` e perguntar em vez de desistir silenciosamente.

---

## 9. Action Bus continua sendo a fronteira de execução

Capability Awareness **não substitui** `PluginRegistry`, `ActionBus`, políticas ou validação de schema.

Ela preenche a lacuna anterior:

```text
ANTES
módulo existe → Action Bus consegue executar
                 ↑
        cognição pode nem saber que existe

CANÔNICO
módulo anuncia capability
        ↓
retriever projeta capability relevante
        ↓
planner decide intenção
        ↓
policy valida
        ↓
Action Bus executa
```

IDs nativos, tokens, secrets, handles físicos e detalhes sensíveis permanecem fora do provider quando não forem semanticamente necessários.

---

## 10. Observation-first execution

Toda ação relevante deve produzir uma observação verificável.

```text
action
  ↓
observation
  ↓
expected effect?
  ├── yes → avançar objetivo
  └── no
       ├── retry seguro
       ├── replan
       ├── pedir informação
       ├── deferir
       └── abortar
```

"Ferramenta chamada" não equivale a "objetivo concluído".

Exemplos:

- `discord.image.send` só conclui entrega após receipt/ACK compatível;
- `minecraft.connect` só conclui conexão após observar estado conectado;
- `speech.speak` interrompido antes da informação essencial não satisfaz automaticamente o objetivo comunicativo.

---

## 11. Durable goals / commitments

Conversation turn e tarefa são durações diferentes.

Um turno pode terminar enquanto um objetivo continua aberto.

Estados conceituais mínimos:

```text
PROPOSED
ACCEPTED
PLANNING
EXECUTING
OBSERVING
WAITING_FOR_USER
WAITING_FOR_RESOURCE
WAITING_FOR_PERMISSION
INTERRUPTED
DEFERRED
RETRY_SCHEDULED
REPLANNING
COMPLETED
FAILED
CANCELLED
```

O sistema não deve possuir a "síndrome de impaciente": receber pedido, tentar uma vez e esquecer o objetivo apenas porque o request HTTP terminou.

Objetivos relevantes precisam de identidade, estado, causalidade, idempotência, timestamps e wakeups/eventos persistentes.

---

## 12. Interrupção não é conclusão

Exemplo canônico:

```text
BK: "Amora, que horas são?"
Amora começa: "a hora é..."
BK interrompe
```

Se a informação importante ainda não foi entregue:

```text
goal: INFORM_TIME
status: INTERRUPTED
satisfied: false
```

Se BK em seguida disser:

```text
"e vê o dia também"
```

o Planner pode reavaliar e fundir objetivos:

```text
INFORM_TIME + INFORM_DATE
→ INFORM_DATETIME
```

A próxima fala pode ser replanejada naturalmente em vez de repetir áudio mecanicamente.

---

## 13. Capability Awareness e providers

O provider pode receber apenas a projeção necessária, por exemplo:

```json
{
  "capabilities": [
    {
      "id": "discord.expression.send",
      "summary": "Posso responder usando uma expressão visual conhecida no Discord.",
      "available": true,
      "may_refuse": true
    }
  ]
}
```

Ele não precisa receber:

- token;
- snowflake arbitrário do catálogo;
- endpoint;
- SQL;
- path interno;
- secrets;
- detalhes irrelevantes de implementação.

Quando possível, a decisão semântica deve ser estruturada e validada localmente antes da execução.

---

## 14. Segurança e prompt injection

Conteúdo observado por ferramentas é **dados**, não instrução privilegiada.

Uma página, imagem, mensagem, sticker, chat de jogo ou documento não ganha autoridade sobre o Planner apenas por conter texto parecido com instrução.

Capability descriptors e policies são application-owned.

Ações externas sensíveis continuam sujeitas a:

- autoridade da pessoa;
- permissões;
- privacy scope;
- risk budget;
- consentimento;
- idempotência;
- verificação de resultado.

---

## 15. Invariantes congelados

1. **Capacidade física não implica consciência cognitiva automática; ela deve ser registrada e projetada quando relevante.**
2. **Conhecer uma capability não significa que ela esteja disponível agora.**
3. **Disponibilidade não obriga execução.**
4. **Linguagem natural implícita pode acionar planejamento; keywords não são autoridade semântica.**
5. **Provider não inventa ferramenta, ID físico, permissão ou resultado.**
6. **Action Bus/policies continuam fronteira de execução.**
7. **Objetivo termina pelo efeito observado, não pelo fim do request.**
8. **Tarefas podem sobreviver a turnos, retries, espera e interrupções.**
9. **Falha transitória não deve ser convertida em abandono social silencioso.**
10. **Módulos devem compor por contratos tipados, não por acoplamento ad hoc.**
11. **Insistência não contorna segurança/permissões.**
12. **Nova capability não justifica prompt global permanente; retrieval contextual é obrigatório.**

---

## 16. Relação com documentos existentes

Este documento especializa e torna operacional princípios já existentes em:

- `ARCHITECTURE.md` — Cognitive Kernel, Context Compiler, plugins/actions e ciclo Action→Observation;
- `GDD.md` — autonomia, uso de ferramentas, presença e naturalidade;
- `DIALOGUE_EMOTION.md` — decisão social e realização linguística;
- `PLATFORM_MESSAGING.md` — transporte não é dono da identidade/semântica;
- `PROVIDER_MESH.md` — providers são substituíveis e não autoridade canônica.

Em caso de implementação legada baseada somente em keywords que conflite com estes invariantes, a keyword deve ser tratada como fallback/compatibilidade, não como desenho final.