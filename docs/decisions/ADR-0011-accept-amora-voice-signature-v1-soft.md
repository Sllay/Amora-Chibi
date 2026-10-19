# ADR-0011 — Aceitar `AMORA_VOICE_SIGNATURE_V1_SOFT` como assinatura vocal aprovada de laboratório

**Status:** ACCEPTED  
**Data:** 2026-10-18  
**Supersedes:** none  
**Superseded by:** none

## Contexto

ADR-0010 congelou `AMORA_CHILD_FINAL_B_PLUS60` como base vocal aprovada de laboratório. A etapa seguinte precisava dar à Amora uma assinatura própria sem voltar a alterar idade/tamanho vocal e sem empilhar efeitos robóticos.

A Voice Kitchen testou `AMORA_VOICE_SIGNATURE_V1_SOFT` em A/B direto contra FINAL_B.

A assinatura mantém intactos:

- target F0 median da FINAL_B;
- max F0 ratio;
- pitch-range base;
- spectral/VTL ratios;
- high-frequency shelf.

Ela adiciona apenas duas mudanças pequenas e determinísticas:

- WORLD aperiodicity scale: `0.965x`;
- camada harmônica soft RMS-matched: drive `1.30`, wet `0.05`.

Continuam desligados:

- pitch shift extra;
- novo formant/VTL;
- bitcrush;
- sample-hold;
- ring modulation.

BK escutou a rodada e aprovou explicitamente a assinatura como perfeita para seguir adiante.

## Decisão

Aceitar como **assinatura vocal aprovada de laboratório**:

```text
AMORA_VOICE_SIGNATURE_V1_SOFT
```

Pipeline congelado desta etapa:

```text
Supertonic 60/40
    ↓
AMORA_CHILD_FINAL_B_PLUS60
    ↓
aperiodicity 0.965x
    ↓
soft harmonic layer 5%
    ↓
AMORA_VOICE_SIGNATURE_V1_SOFT
```

A partir desta decisão, emoção/prosódia não deve reescrever a VoiceSignature. Ela pode apenas modular expressão ao redor da identidade fixa.

## Invariante para a próxima etapa

```text
identidade vocal = fixa
expressão emocional = variável e bounded
```

Portanto:

- felicidade não troca timbre;
- raiva não autoriza pitch global;
- carinho não reabre child/VTL;
- tristeza não pode degradar inteligibilidade;
- falha do módulo emocional deve cair para expressão neutra, nunca para silêncio ou outra voz.

## O que esta decisão NÃO significa

Ainda não é promoção Android/produção. O caminho de produção continua provisório até existir implementação móvel equivalente, artifact/APK e certificação física no Galaxy A15.

A próxima etapa é validar um `VoiceProsodyPlanner` modular, com expressão emocional bounded e fail-safe neutro sobre esta assinatura congelada.
