# ADR-0002 — Voz local pt-BR como caminho normal

**Status:** ACCEPTED  
**Data:** 2026-10-10  
**Supersedes:** estratégia Gemini TTS primary → Android TTS fallback  
**Superseded by:** none

## Contexto

A Amora usava Gemini TTS expressivo quando disponível e Android TTS como fallback.

Na prática, a quota gratuita do Gemini TTS era pequena demais para conversa casual prolongada. Quando a quota acabava, a voz mudava abruptamente para um TTS Android muito mais robótico.

Isso criava dois problemas:

1. indisponibilidade frequente da voz expressiva;
2. quebra perceptível de identidade vocal entre falas.

A meta operacional do projeto é custo zero sempre que praticável, sem tornar um free tier específico requisito para a Amora “existir”.

## Evidência / problema observado

Durante uso físico no Galaxy A15:

- Gemini TTS atingiu `429 RESOURCE_EXHAUSTED`;
- o fluxo precisou cair para Android TTS;
- a mudança de timbre foi percebida como voz “Google Tradutor”;
- retries/cooldown mostraram que otimizar requests não resolveria a inconsistência fundamental de identidade;
- depender de quota remota para toda fala não atende a filosofia de uso prolongado.

## Alternativas consideradas

- continuar Gemini TTS e otimizar chamadas;
- Android TTS nativo com pitch/rate/EQ;
- Kokoro mobile;
- Supertonic;
- Pocket TTS;
- Piper/VITS via sherpa-onnx;
- voz remota paga.

## Decisão

O caminho normal da voz passa a ser **TTS neural local no Android**.

Primeiro backend V1:

```text
VoiceIdentity pt-BR
↓
Prosody Planner
↓
sherpa-onnx
↓
Piper/VITS pt-BR provisório
↓
PCM local
↓
AudioTrack
```

Regras:

- `pt-BR` é obrigatório;
- modelo/timbre normal deve ser pinado;
- Voice Pack pode ser baixado uma vez e verificado por SHA-256;
- síntese normal deve funcionar offline;
- Android TTS nativo fica somente como bootstrap/emergência;
- Gemini TTS fica fora do caminho normal;
- futura `amora-voice-v1` própria deve usar material com direitos claros.

## Motivos

- identidade vocal consistente;
- zero custo por fala;
- independência de quota;
- funcionamento offline;
- runtime sherpa-onnx já é compatível com a direção Android do projeto;
- Piper/VITS é suficientemente leve para ser candidato real ao Galaxy A15.

## Consequências

A qualidade final depende de benchmark físico no A15.

O modelo provisório não é automaticamente a voz definitiva da Amora.

Tecnologias alternativas, como Pocket TTS, podem ser avaliadas em laboratório, mas não substituem o caminho normal sem benchmark e novo ADR.

## Critério para revisitar

Revisitar se outro backend local:

- oferecer pt-BR comprovadamente melhor;
- mantiver latência/CPU/RAM/temperatura aceitáveis no A15;
- tiver licença adequada;
- não introduzir custo obrigatório ou dependência de rede.
