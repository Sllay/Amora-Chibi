# AMORA — Identidade Vocal Canônica

**Status:** CANÔNICO / ESPECIFICAÇÃO DE ALVO  
**Atualizado em:** 2026-10-16  
**Runtime de produção atual:** sherpa-onnx + Piper/VITS Faber provisório  
**Laboratório atual:** Supertonic 3 INT8 via sherpa-onnx  
**Alvo final:** `amora-voice-v1` como identidade vocal versionada = backend aprovado + speaker/base + VoiceSignature + prosódia bounded.

---

## 1. Princípio

> A voz é parte da identidade contínua da Amora, mas a identidade vocal não deve ficar presa a um único checkpoint treinado.

O backend de TTS é capacidade substituível. A identidade percebida deve permanecer estável por meio de seleção de base, `VoiceSignature`, `VoiceProfile` e parâmetros de prosódia versionados.

A voz normal deve priorizar execução local/offline e não depender de quota remota.

---

## 2. Perfil vocal alvo

A voz canônica da Amora deve soar como:

- feminina;
- brasileira / pt-BR natural e inteligível;
- jovem adulta;
- doce sem infantilização;
- madura o suficiente para não soar como anime/chibi exageradamente aguda;
- ainda jovem, sem peso de voz muito madura/mais velha;
- região base entre médio e médio-grave, com brilho/agudos naturais quando a expressão pedir;
- dicção clara, mas não de narradora, locutora, assistente ou anúncio;
- ritmo base moderado, ligeiramente vivo/brasileiro, sem falar acelerado por padrão;
- energia presente, espontânea e conversacional, sem hiperatividade artificial;
- microvariações naturais de ritmo, pausa e intensidade;
- capaz de intimidade, brincadeira, firmeza, baixa energia e irritação sem perder a mesma identidade-base.

Não é objetivo imitar pessoa pública, pessoa real sem autorização ou voz cuja licença impeça o uso pretendido.

---

## 3. Componentes da identidade vocal

A identidade canônica é composta por quatro camadas separadas:

```text
TtsBackend
    ↓
speaker/base aprovada
    ↓
VoiceSignature AMORA_CANONICAL
    ↓
Prosody Planner bounded
```

### 3.1 TtsBackend

Responsável por produzir fala base inteligível. Pode ser substituído se outro backend local oferecer melhor pt-BR, licença e desempenho.

O backend **não é sozinho a identidade da Amora**.

### 3.2 Speaker/base

Quando o backend possuir múltiplos speakers, um speaker-base deve ser pinado para a voz normal após benchmark físico. Não escolher speaker aleatoriamente entre sessões.

### 3.3 VoiceSignature

`VoiceSignature` é a camada determinística/versionada que transforma o PCM base para aproximá-lo da identidade vocal alvo.

Transforms candidatos, apenas quando tecnicamente aprovados:

- pitch delta pequeno;
- formant/ressonância;
- EQ/cor tonal;
- de-essing;
- compressão leve;
- textura sintética discreta;
- ganho/limiter;
- outros transforms bounded e auditáveis.

A ordem, intensidade e implementação desses transforms fazem parte da versão da assinatura.

**Regra:** inteligibilidade pt-BR tem prioridade sobre efeito estilístico.

### 3.4 Prosody Planner

Humor/intenção podem alterar levemente rate, energia, pausas, firmeza, suavidade e timing, dentro de limites pequenos.

Emoção não escolhe parâmetros crus diretamente; o `Prosody Planner` recebe estado interno + intenção + tipo de fala e produz uma configuração bounded.

---

## 4. Identidade canônica vs fantasias

A Amora pode possuir múltiplos `VoiceProfile`, mas apenas um é a voz normal canônica.

```text
AMORA_CANONICAL  -> identidade normal persistente
CHIBI            -> fantasia temporária
SYNTH            -> fantasia temporária
MASC_JOKE        -> fantasia futura
outros           -> experimentais
```

Perfis temporários:

- devem ser ativados explicitamente;
- não alteram self/memória/identidade da Amora;
- não substituem silenciosamente a voz normal;
- podem usar parâmetros mais exagerados porque são efeitos/fantasias assumidos.

---

## 5. Mapeamento emocional

Exemplos conceituais, não fórmulas fixas:

### Baixa energia / tédio

- ritmo um pouco mais lento;
- pausas maiores;
- energia menor;
- respostas possivelmente mais curtas pelo DialoguePlan;
- sem monotonia robótica forçada.

### Alegria / animação

- energia e ritmo discretamente maiores;
- pausas mais leves;
- brilho natural;
- sem acelerar até parecer síntese artificial.

### Irritação / raiva

- comportamento social fica mais firme antes da voz mudar;
- wording pode ficar mais curto/direto conforme DialoguePlan;
- prosódia pode ganhar firmeza/tensão leve;
- não representar raiva simplesmente como pitch/volume altos.

### Afeto / intimidade

- ritmo levemente mais macio;
- pausas naturais;
- menor dureza;
- sem infantilizar a voz.

---

## 6. Backend e licença

Qualquer backend/speaker candidato precisa de provenance e licença compatíveis com o uso pretendido.

Aceitável:

- modelo local com licença permissiva/compatível;
- voz/speaker incluídos no modelo com termos adequados;
- material próprio com consentimento explícito;
- modelo próprio treinado no futuro, caso volte a ser vantajoso e reproduzível.

Não aceitável como base canônica:

- clone de pessoa pública sem autorização;
- voz real/comercial cuja licença impeça uso/derivação necessários;
- artefato de origem obscura;
- provider remoto que mude de voz por quota como caminho normal.

A provenance deve ficar registrada junto da versão do voice pack/manifesto da assinatura.

---

## 7. Estratégia atual de implementação

Treinar/fine-tunar um checkpoint próprio **não é mais requisito da V1**. O ADR-0008 foi superseded pelo ADR-0009.

Fluxo atual:

```text
backend local pronto + rights-clear
        ↓
benchmark de speakers/base
        ↓
seleção física no Galaxy A15
        ↓
VoiceSignature determinística
        ↓
AMORA_CANONICAL
        ↓
Prosody Planner bounded
        ↓
PCM / AudioTrack
```

Treino próprio continua permitido como pesquisa futura se houver evidência clara de ganho e compute/dataset reproduzíveis.

---

## 8. Voice Lab atual

Existe um laboratório Android isolado usando **Supertonic 3 INT8 via sherpa-onnx**.

Objetivo:

- ouvir todos os speakers disponíveis;
- comparar as mesmas frases pt-BR;
- medir RTF/latência;
- escolher uma base pela audição, não pelo nome;
- testar presets temporários sem alterar produção.

Presets atuais do laboratório:

- `NATURAL`;
- `AMORA_SOFT`;
- `AMORA_SYNTH`;
- `CHIBI`.

Esses presets atuais mexem principalmente em speed/silence/steps; **ainda não são a VoiceSignature final** e não fazem pitch/formant DSP.

O `LocalVoiceEngine` de produção continua usando Faber até promoção explícita após teste físico.

---

## 9. Gate físico

A voz só pode ser promovida como `AMORA_CANONICAL` após teste real no Galaxy A15.

Avaliar pelo menos:

- pt-BR real e inteligível;
- pronúncia de Amora/BK/gírias/frases difíceis;
- consistência de speaker;
- latência de primeira fala;
- RTF;
- RAM/CPU;
- temperatura;
- estabilidade prolongada;
- barge-in/playback;
- funcionamento offline;
- ausência de chamadas TTS remotas;
- qualidade da VoiceSignature;
- diferença clara entre identidade canônica e fantasias temporárias.

CI não substitui esse gate.

---

## 10. Portabilidade futura

A `VoiceSignature` deve ser especificada de modo que possa ser reproduzida em mais de um cliente quando tecnicamente viável.

Direção planejada:

```text
Android TtsBackend ─┐
                    ├─ VoiceSignature AMORA_CANONICAL
Web/PWA TtsBackend ─┘
```

O backend pode diferir por plataforma; o objetivo é preservar a mesma identidade percebida e parâmetros versionados, não obrigar todos os clientes a usar exatamente o mesmo runtime.

---

## 11. Estado atual

- Faber masculino = voz técnica/provisória de produção que certificou o pipeline local.
- Supertonic 3 INT8 = candidato de laboratório já integrado de forma isolada.
- voz final = **ainda não escolhida**; depende do teste físico de BK.
- próximo passo = testar speakers/presets no Galaxy A15, selecionar a melhor base e então implementar/calibrar a VoiceSignature real.
