# ADR-0009 — TTS pronto + VoiceSignature como estratégia da voz canônica

**Status:** ACCEPTED  
**Data:** 2026-10-16  
**Supersedes:** ADR-0008  
**Superseded by:** none

## Contexto

A V1 precisava de uma voz feminina pt-BR estável, local/offline e reconhecível como Amora. O caminho anterior tornava treino/fine-tuning próprio requisito direto para concluir a identidade vocal.

Na prática, a sequência de experimentos com Piper/VITS em GPU gratuita mostrou custo operacional alto demais para o objetivo do projeto:

- runtime gratuito instável/resetável;
- quota de Colab interrompendo ciclos;
- dependência de dataset/checkpoints/cache efêmeros;
- warmstart/híbridos sensíveis e difíceis de reproduzir;
- várias horas gastas restaurando ambiente em vez de avaliar produto;
- melhora parcial de inteligibilidade sem garantia de identidade vocal consistente.

Isso caracterizou patch loop no processo de treino. A meta continua sendo R$ 0 sempre que praticável e a voz precisa avançar como produto, não depender de infraestrutura de treino temporária.

Ao mesmo tempo, o Android já possui sherpa-onnx fisicamente comprovado no Galaxy A15 e a branch principal recebeu um `Voice Lab` isolado para avaliar Supertonic 3 INT8 sem tocar no `LocalVoiceEngine` de produção.

## Método anterior

```text
fonte/dataset rights-clear
↓
fine-tuning Piper/VITS
↓
export ONNX
↓
modelo single-speaker próprio
↓
sherpa-onnx Android
```

Esse caminho deixa de ser requisito da V1. Treino próprio continua sendo possibilidade futura, não dependência de fechamento.

## Alternativas consideradas

- insistir no treino/fine-tuning em Colab/Kaggle;
- adotar voz pronta sem transformação e aceitar identidade do speaker como identidade da Amora;
- usar TTS remoto expressivo;
- usar TTS local pronto + camada determinística de assinatura vocal;
- manter Android TTS nativo como voz principal.

## Decisão

A estratégia canônica passa a separar **backend de fala** de **identidade vocal percebida**.

```text
Dialogue / texto falável
↓
Prosody Planner bounded
↓
TtsBackend local pt-BR
↓
PCM base
↓
VoiceSignature da Amora
↓
Audio output
```

### 1. TtsBackend

O backend é substituível. Deve priorizar:

- pt-BR inteligível;
- execução local/offline quando praticável;
- licença/provenance adequadas;
- desempenho aceitável em mobile;
- previsibilidade.

Supertonic 3 INT8 via sherpa-onnx é o primeiro candidato do laboratório atual, **não é automaticamente a voz final**.

### 2. VoiceSignature

A identidade vocal não fica presa a um único modelo treinado. Ela será expressa por uma camada determinística/versionada capaz de aplicar, conforme o backend suportar e os testes aprovarem:

- pitch delta pequeno;
- formant/ressonância;
- EQ/cor tonal;
- de-essing;
- compressão/controle de dinâmica;
- textura sintética discreta;
- ganho/limiter;
- outros transforms bounded e auditáveis.

Nenhum efeito deve sacrificar inteligibilidade pt-BR apenas para soar diferente.

### 3. VoiceProfile

Separar identidade canônica de fantasias temporárias.

```text
AMORA_CANONICAL  -> voz normal / identidade persistente
CHIBI            -> fantasia
SYNTH            -> fantasia
MASC_JOKE        -> fantasia futura
outros           -> experimentais
```

Trocar para uma fantasia deve ser explícito e temporário; não altera a identidade canônica nem memória/self da Amora.

### 4. Promoção para produção

O `Voice Lab` permanece isolado. `LocalVoiceEngine` atual não é substituído até o candidato vencer teste físico no Galaxy A15.

A promoção exige pelo menos:

- português claramente compreensível;
- speaker/base escolhidos por audição comparativa;
- RTF e latência aceitáveis;
- RAM/CPU/temperatura aceitáveis;
- estabilidade longa;
- licença/provenance registradas;
- assinatura vocal aprovada por BK;
- ausência de regressão em barge-in/playback.

## Motivos

- remove treino de GPU gratuita do caminho crítico;
- permite progresso imediato e reproduzível;
- mantém custo por fala em zero quando local;
- preserva modularidade: backend pode mudar sem redefinir quem a Amora é;
- cria suporte natural a perfis/fantasias vocais;
- combina com a filosofia `provider != identidade` já usada no Core;
- permite portar a mesma VoiceSignature para Android e futuro Web/PWA.

## Consequências

- `amora-voice-v1` deixa de significar obrigatoriamente "um checkpoint treinado por nós" e passa a significar uma **identidade vocal versionada** composta por backend aprovado + speaker/base + VoiceSignature + parâmetros de prosódia.
- ADR-0008 fica `SUPERSEDED` como estratégia obrigatória de fechamento da V1.
- Faber permanece técnico/provisório até promoção física de novo backend.
- Supertonic e seus presets atuais são laboratório, não canon de identidade.
- DSP avançado só deve ser adicionado depois de escolher uma base que já fale português bem.
- um modelo próprio treinado pode ser revisitado futuramente se houver compute/dataset/benefício suficientes.

## Critério para revisitar

Revisitar se:

- um backend local novo oferecer qualidade muito superior;
- a VoiceSignature não conseguir preservar identidade ao trocar backend;
- DSP móvel causar custo/artefatos inaceitáveis;
- surgir compute/dataset rights-clear que torne um modelo próprio claramente superior e reproduzível;
- a futura PWA exigir estratégia de áudio incompatível com a implementação móvel atual.
