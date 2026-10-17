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

Supertonic 3 INT8 via sherpa-onnx foi o primeiro candidato do laboratório e, após comparação física por BK no Galaxy A15, **speaker 0 foi selecionado como base canônica V1**.

### 2. VoiceSignature

A identidade vocal não fica presa a um único modelo treinado. Ela é expressa por uma camada determinística/versionada capaz de aplicar, conforme o backend suportar e os testes aprovarem:

- pitch delta pequeno quando houver implementação de qualidade aprovada;
- formant/ressonância quando houver implementação de qualidade aprovada;
- EQ/cor tonal;
- de-essing quando necessário;
- compressão/controle de dinâmica;
- textura sintética discreta;
- ganho/limiter;
- outros transforms bounded e auditáveis.

Nenhum efeito deve sacrificar inteligibilidade pt-BR apenas para soar diferente.

A primeira implementação real usa EQ, saturação suave, textura harmônica paralela discreta, compressor e limiter. Não declaramos pitch/formant reais enquanto o runtime não os implementar com qualidade suficiente.

### 3. VoiceProfile

Separar identidade canônica de expressão/fantasias temporárias.

```text
NATURAL  -> voz normal / identidade persistente
SOFT     -> expressão mais macia
SHY      -> expressão mais contida
SYNTH    -> fantasia sintética mais evidente
CHIBI    -> fantasia mais viva/brilhante
```

O profile nunca pode trocar silenciosamente o speaker/base da identidade.

### 4. Profile servido pelo Core

Os parâmetros de tuning da assinatura e expressão ficam em um documento versionado no Core (`amora_voice_profile.json`). O Android busca o documento autenticado, valida contra `VoiceIdentity.AMORA_V1`, guarda o último profile válido e usa ETag para revalidação barata.

Objetivo operacional: depois de uma build estrutural instalada, calibração de parâmetros não exige novo APK.

Falha no Core/profile não quebra a fala local: o Android conserva o último profile válido ou um default seguro empacotado.

### 5. Emoção

O Emotion Engine/Core continua sendo autoridade emocional. `SpeechStyle` entrega sinais bounded (`rate`, `pitch`, `volume`, `valence`, `arousal`) para a voz.

A integração atual do Supertonic via sherpa-onnx não expõe um controle nativo de emoção comprovado. Portanto a V1 não inventa suporte inexistente; usa os sinais do cérebro sobre o mesmo speaker/base e a mesma VoiceSignature.

Caso um backend futuro ofereça emoção nativa real, o adapter pode consumi-la atrás deste contrato após teste físico.

### 6. Promoção para produção

A seleção de base já ocorreu. A promoção final/merge da assinatura exige:

- português claramente compreensível;
- speaker 0 reconhecível após VoiceSignature;
- assinatura aprovada por BK sem caráter metálico/robótico indesejado;
- RTF e latência aceitáveis;
- RAM/CPU/temperatura aceitáveis;
- estabilidade longa;
- licença/provenance registradas;
- ausência de regressão em barge-in/playback;
- hot tuning do profile funcionando sem rebuild de APK.

## Motivos

- remove treino de GPU gratuita do caminho crítico;
- permite progresso imediato e reproduzível;
- mantém custo por fala em zero quando local;
- preserva modularidade: backend pode mudar sem redefinir quem a Amora é;
- cria suporte natural a perfis/fantasias vocais;
- combina com a filosofia `provider != identidade` já usada no Core;
- permite portar a mesma VoiceSignature para Android e futuro Web/PWA;
- separa mudança estrutural de app de tuning auditivo iterativo.

## Consequências

- `amora-voice-v1` significa uma identidade vocal versionada composta por backend aprovado + speaker/base + VoiceSignature + parâmetros de prosódia/expressão.
- ADR-0008 fica `SUPERSEDED` como estratégia obrigatória de fechamento da V1.
- Supertonic 3 INT8 speaker 0 é a base canônica em certificação.
- Faber passa a ser legado técnico de rollout e pode ser removido dos assets depois da aceitação física do novo caminho.
- DSP avançado só entra quando houver ganho auditivo real e custo mobile aceitável.
- um modelo próprio treinado pode ser revisitado futuramente se houver compute/dataset/benefício suficientes.

## Critério para revisitar

Revisitar se:

- um backend local novo oferecer qualidade muito superior;
- a VoiceSignature não conseguir preservar identidade ao trocar backend;
- DSP móvel causar custo/artefatos inaceitáveis;
- surgir compute/dataset rights-clear que torne um modelo próprio claramente superior e reproduzível;
- a futura PWA exigir estratégia de áudio incompatível com a implementação móvel atual.
