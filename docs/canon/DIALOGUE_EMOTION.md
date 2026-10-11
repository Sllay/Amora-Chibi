# AMORA — Diálogo, Emoção e Realização Linguística

**Status:** CANÔNICO / ATUAL  
**Atualizado em:** 2026-10-11  
**Escopo:** contrato entre eventos, Emotion Engine, Dialogue Orchestrator, providers e expressão vocal.

---

## 1. Invariante

> O provider não decide quem a Amora é, o que ela sente nem qual ato social ela executa.

O Core local é autoridade sobre:

- estado emocional;
- relações;
- facts/self-facts;
- stance/opinião quando já decidida localmente;
- atitude relacional;
- dialogue/social act;
- follow-up policy;
- help-offer policy;
- closing mode;
- grounding constraints;
- memória persistente.

O provider é um **realizador linguístico substituível**. Ele transforma uma decisão local em linguagem natural dentro de um espaço autorizado.

---

## 2. Loop emocional fechado

O Emotion Engine só é útil se acontecimentos reais da vida da Amora o alimentarem.

Fluxo canônico:

```text
percepção / fala / ação / resultado
        ↓
evento canônico
        ↓
Interaction Appraisal
        ↓
Emotion Engine
        ├─ Emotion Event
        ├─ Global Mood
        └─ Entity Affect
        ↓
Dialogue / Autonomy / Avatar / Prosody
        ↓
ação / fala
        ↓
resultado observado
        └──────────────→ novo evento
```

Ler `current_mood()` sem produzir appraisals a partir de eventos reais é implementação incompleta.

### 2.1 Appraisal

Appraisal deve continuar determinístico, auditável e bounded. O provider pode fornecer interpretação auxiliar no futuro, mas não é a única fonte de verdade emocional.

Dimensões existentes como valence de resultado, goal relevance, novelty, expectedness, agency, control, intentionality, norm violation e relationship relevance devem ser derivadas de fatos/eventos observáveis quando possível.

Nem toda fala precisa gerar emoção relevante. Eventos abaixo do threshold podem produzir nenhum Emotion Event.

---

## 3. Estado emocional != comportamento != prosódia

As três camadas são separadas.

```text
ESTADO INTERNO
valence / arousal / dominance
anger / irritation / fear / affection / trust / gratitude...
        ↓
COMPORTAMENTO SOCIAL
stance / paciência / distância / assertividade / curiosidade /
brincadeira / limite / vontade de continuar / closing mode
        ↓
EXPRESSÃO
wording permitido + ritmo + pausas + energia + microvariação vocal
```

Exemplos:

- `anger` não significa simplesmente aumentar pitch/volume;
- tédio/baixa energia não significa apenas diminuir speed;
- alegria não autoriza trocar a identidade vocal;
- afeto não autoriza inventar facts nem violar limites de privacidade.

A emoção altera **como a Amora se expressa e age dentro da própria identidade**; não troca a identidade.

---

## 4. DialoguePlan autoritativo

O `DialoguePlan` deve crescer para representar decisões sociais suficientes antes do provider.

Campos/semânticas esperadas quando aplicáveis:

- `act`;
- `objective`;
- `response_moves`;
- `depth`;
- `stance`;
- `relational_attitude`;
- `closing_mode`;
- `ask_followup`;
- `offer_help`;
- facts permitidos/desconhecidos;
- grounding constraints;
- tone/energy tags derivados de estado local;
- reason codes.

Atos V1 devem cobrir pelo menos:

- acknowledgement;
- self fact;
- disclosure;
- support request;
- answer question;
- continue topic;
- autonomous initiation;
- reflection;
- express opinion;
- disagree;
- tease/playful reaction;
- complain;
- relational reaction;
- set boundary.

---

## 5. RealizationFrame — hard control

Não basta serializar o `DialoguePlan` no system prompt e pedir para o modelo obedecer.

O caminho aprovado é:

```text
DialoguePlan
    ↓
RealizationRequest estruturado
    ↓
provider retorna RealizationFrame estruturado
    ↓
validator local
    ↓
renderização textual final
    ↓
SpeechNormalizer / queue
```

Um `RealizationFrame` contém moves explicitamente autorizados, por exemplo:

```json
{
  "moves": [
    {"kind": "react", "text": "..."},
    {"kind": "advance_topic", "text": "..."}
  ],
  "closing": "none"
}
```

O schema pode variar por provider, mas a semântica local não.

### 5.1 Regras

- move não autorizado pelo plano é rejeitado;
- `ask_followup=false` impede move de follow-up;
- `offer_help=false` impede move de offer-help;
- self-fact unknown não pode virar fact inventado;
- facts conhecidos precisam permanecer grounded;
- o provider não pode criar novo objetivo social escondido em prose livre;
- saída estruturada inválida não entra na fila de fala.

Structured generation/JSON schema/grammar é preferível quando o provider suportar. Quando não suportar, parse + validator local continua obrigatório.

### 5.2 Não usar blacklist como arquitetura

Blacklist de frases pode existir apenas como sinal de regressão/telemetria. Ela não é mecanismo primário de controle.

O objetivo é controlar **atos/moves/facts**, não tentar enumerar toda frase ruim possível.

---

## 6. Realização local quando o provider é desnecessário

Atos com semântica completamente resolvida podem ser realizados localmente sem LLM, especialmente:

- vocativo simples;
- backchannel;
- self-fact curto;
- confirmação;
- limite local já decidido;
- mensagens operacionais não sociais.

Isso reduz latência, quota e superfície de comportamento de assistente.

---

## 7. Isolamento de testes

Testes técnicos não devem ensinar biografia, relação ou emoção persistente à Amora por acidente.

Eventos/turnos de teste devem poder carregar metadata equivalente a:

```text
test_mode = true
memory_consolidation = false
relationship_learning = false
emotion_persistence = false
```

Regras:

- o pipeline técnico pode continuar completo para testar STT/cognição/diálogo/voz;
- memória de longo prazo não consolida conteúdo do teste;
- Relationship Graph não aprende fatos relacionais do teste;
- Emotion Engine pode calcular um resultado efêmero para validação, mas não persiste mood/affect quando `emotion_persistence=false`;
- logs/journal podem registrar que houve teste para auditoria;
- dados já contaminados devem ser limpos seletivamente, não por reset indiscriminado de toda a identidade.

---

## 8. Observabilidade obrigatória

O estado operacional deve permitir distinguir:

- último appraisal significativo;
- último Emotion Event;
- mood atual;
- affect dirigido ao addressee atual;
- DialoguePlan do último turno;
- RealizationFrame aceito/rejeitado;
- reason codes de validação;
- se o turno estava em `test_mode`.

`mood_valence=0.0` por longos períodos de conversa deve ser investigável como ausência de appraisal, threshold legítimo ou decay — nunca como caixa-preta.

---

## 9. Critério de saída do Step 2

Step 2 fecha quando:

1. eventos normais de conversa alimentam Emotion Engine de forma bounded e observável;
2. estado emocional influencia comportamento social antes da prosódia;
3. provider não possui liberdade de alterar dialogue act/follow-up/help/facts;
4. RealizationFrame passa por validator local antes da fala;
5. testes técnicos podem atravessar o pipeline sem contaminar memória/relacionamento/mood persistente;
6. regressões conhecidas de atendimento genérico deixam de ser produzidas como novo conteúdo do provider em cenários de replay;
7. testes determinísticos e CI cobrem os contratos acima.
