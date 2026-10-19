# ADR-0010 — Congelar `AMORA_CHILD_FINAL_B_PLUS60` como base vocal aprovada de laboratório

**Status:** ACCEPTED  
**Data:** 2026-10-18  
**Supersedes:** none  
**Superseded by:** none

## Contexto

Após abandonar a estratégia de treino/fine-tuning próprio como requisito da V1 (ADR-0009), a Voice Kitchen passou a investigar uma base Supertonic que pudesse receber uma `VoiceSignature` determinística sem cair no efeito de pitch global/chipmunk.

A sequência de testes estabeleceu alguns fatos auditivos importantes:

- o mix de estilo **60% speaker 0 + 40% speaker 4** foi preferido à base isolada;
- pitch global / playback-rate alto foi rejeitado por soar como esquilo/alien;
- formant shift uniforme foi rejeitado por soar fino/nasal, como adulta falando com nariz tampado;
- WORLD com F0 bounded + vocal-tract shaping não uniforme finalmente mudou a percepção de idade/tamanho vocal de forma útil;
- `WORLD_CHILD_CLEAR_POLISHED` ficou mais limpa e inteligível;
- `WORLD_CHILD_PLUS_POLISHED` ficou um pouco mais natural;
- a comparação final A/B foi criada apenas dentro desse intervalo já aprovado.

BK escolheu explicitamente **B** no A/B final.

## Decisão

Congelar como **base vocal aprovada de laboratório**:

```text
AMORA_CHILD_FINAL_B_PLUS60
```

Pipeline experimental correspondente:

```text
Supertonic style base
  60% speaker 0
  40% speaker 4
        ↓
WORLD analysis
        ↓
F0 median bounded / contour-preserving
        ↓
non-uniform vocal-tract spectral warp
        ↓
gentle high-frequency shelf
        ↓
AMORA_CHILD_FINAL_B_PLUS60
```

Parâmetros da candidata B registrados em `tools/voice_kitchen/apply_world_final_ab.py`:

- target F0 median: **284.8 Hz**;
- max F0 ratio: **1.186x**;
- pitch-range scale: **1.048x**;
- spectral ratios:
  - 0 Hz → 1.000x;
  - 250 Hz → 1.000x;
  - 700 Hz → 1.126x;
  - 1500 Hz → 1.111x;
  - 3000 Hz → 1.068x;
  - 5000 Hz → 1.020x;
  - 8000 Hz → 1.000x;
- aperiodicity scale: **1.0**;
- high-frequency shelf: **-2.36 dB**.

## O que esta decisão significa

A fase de busca por **idade/tamanho vocal base** fica encerrada por padrão.

`AMORA_CHILD_FINAL_B_PLUS60` passa a ser o ponto de partida para a próxima fase: construir a **assinatura vocal-synth própria da Amora** sem reabrir os parâmetros de child/VTL a cada experimento.

A nova fase deve preservar a base B e comparar qualquer textura sintética contra ela limpa.

## O que esta decisão NÃO significa

Esta decisão **não promove a voz para produção** e **não declara `AMORA_CANONICAL` pronta**.

Continuam verdadeiros:

- `LocalVoiceEngine` de produção permanece Faber provisório até promoção explícita;
- Voice Kitchen/CI não substituem teste físico no Galaxy A15;
- ainda falta definir/aprovar a camada de textura/identidade vocal-synth;
- ainda falta integrar uma implementação móvel equivalente e medir latência/RTF/RAM/CPU/temperatura;
- qualquer diferença entre o runtime Python/WORLD da cozinha e a implementação Android precisa ser validada por A/B físico;
- inteligibilidade pt-BR continua tendo prioridade sobre estilização.

## Regra anti-patch-loop

Não voltar a ajustar F0, child-VTL ou o mix 60/40 apenas porque um experimento de textura synth ficou ruim.

A próxima investigação deve atribuir o problema à camada correta:

```text
base B aprovada
    ↓
textura / VoiceSignature synth experimental
    ↓
A/B
```

Se a textura synth piorar naturalidade ou inteligibilidade, ela deve ser corrigida/removida sem destruir a base B.

Reabrir a base só exige evidência nova clara, por exemplo:

- regressão física no Galaxy A15;
- pronúncia/inteligibilidade inadequada em uso real;
- artefato inevitável causado pelo próprio pipeline B;
- backend novo claramente superior.

## Próximo passo

Criar a Voice Kitchen da **assinatura vocal-synth** em cima de `AMORA_CHILD_FINAL_B_PLUS60`, com intensidade discreta e identidade própria, mantendo a estética de sintetizador vocal como camada separada da idade/tamanho da voz.
