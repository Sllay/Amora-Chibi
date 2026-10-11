# ADR-0006 — Realização linguística estruturada e validada

**Status:** ACCEPTED  
**Data:** 2026-10-11  
**Builds on:** ADR-0003 — semântica social pertence ao Core  
**Superseded by:** none

## Contexto

Mesmo com `DialoguePlan` local autoritativo, a implementação atual ainda serializa o plano em texto no system prompt e pede ao provider para respeitá-lo. Isso reduz regressões, mas continua dependendo de obediência probabilística do modelo e permite reflexos de chatbot/assistente, como follow-ups, ofertas de ajuda ou metaficção não autorizados.

Blacklist crescente de frases não resolve o problema estrutural.

## Decisão

A saída do provider para diálogo social passa a ser tratada como **realização estruturada**, não prose livre autoritativa.

Fluxo:

```text
DialoguePlan local
↓
RealizationRequest
↓
provider produz RealizationFrame estruturado
↓
validator local
↓
renderização textual aceita
↓
SpeechNormalizer / fila de voz
```

O Core define quais moves são permitidos no turno. O provider escolhe wording apenas dentro desses moves.

Quando suportado, usar structured output / JSON schema / grammar/constrained generation. Quando o provider não oferecer enforcement no decoder, parse e validação local continuam obrigatórios.

## Regras mínimas

- move não autorizado é rejeitado;
- `ask_followup=false` não pode gerar follow-up;
- `offer_help=false` não pode gerar oferta de atendimento;
- self-fact missing não pode virar fato inventado;
- facts grounded precisam corresponder ao estado canônico;
- saída inválida não é falada;
- falha de realization pode cair para realização local segura quando aplicável;
- atos triviais já resolvidos podem não usar provider.

## Alternativas rejeitadas

- aumentar indefinidamente o system prompt;
- blacklist de frases típicas de chatbot;
- trocar de modelo esperando que o prior social desapareça;
- deixar o provider decidir o dialogue act e validar somente depois.

## Consequências

O `DialoguePlan` precisa carregar semântica suficiente para permitir validação útil: stance, relational attitude, closing mode, moves e constraints quando aplicáveis.

Providers ficam mais substituíveis porque deixam de possuir a política social final.

A linguagem continua natural; o que fica rígido é a **estrutura semântica**, não a frase exata.

## Critério de saída

- testes determinísticos provam rejeição de moves não autorizados;
- cenários conhecidos de atendimento genérico não chegam à fila de fala;
- self-facts permanecem grounded;
- Cognitive Replay permite comparar realizadores sem mutar estado.
