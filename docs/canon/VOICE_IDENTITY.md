# AMORA — Identidade Vocal Canônica

**Status:** CANÔNICO / IMPLEMENTAÇÃO V1 EM CERTIFICAÇÃO FÍSICA  
**Atualizado em:** 2026-10-16  
**Runtime de produção em certificação:** sherpa-onnx + Supertonic 3 INT8 / speaker 0  
**Identidade:** `amora-voice-v1` = base aprovada + VoiceSignature + prosódia bounded + expressão do Emotion Engine.

---

## 1. Princípio

> A voz base é identidade; emoção e estilo modificam essa identidade sem trocar a pessoa percebida.

A voz normal da Amora deve executar local/offline no Android. Providers remotos continuam opcionais e nunca substituem silenciosamente a identidade local.

O backend de TTS não é sozinho a identidade. A identidade percebida nasce da composição:

```text
Supertonic 3 INT8 / speaker 0
        ↓
PCM base
        ↓
AMORA VoiceSignature
        ↓
expressão/prosódia bounded vinda do Core
        ↓
AudioTrack
```

---

## 2. Seleção física da base

BK comparou fisicamente os speakers do Supertonic Voice Lab no Galaxy A15 em 2026-10-16.

Resultado aceito:

- speaker **0** foi a melhor voz feminina e a mais próxima da identidade desejada;
- os speakers masculinos foram descartados para a identidade normal;
- pequenas variações naturais de prosódia entre gerações são aceitáveis quando mantêm identidade e inteligibilidade;
- artefatos ocasionais/“flipadas” do TTS são defeitos a monitorar/tunar, não uma característica desejada;
- os presets antigos do Voice Lab quase não alteravam a percepção e não são a assinatura final.

A V1 portanto pina:

```text
backend    = supertonic3-int8
speaker_id = 0
language   = pt
locale     = pt-BR
```

Nenhuma atualização remota de profile pode trocar silenciosamente backend/speaker/idioma desta identidade.

---

## 3. Perfil vocal alvo

A voz canônica deve soar:

- feminina;
- pt-BR natural e inteligível;
- jovem adulta;
- doce sem infantilização;
- conversacional, não locutora/assistente;
- limpa e presente;
- com leve brilho/“ar” fantasioso;
- estilizada o bastante para não parecer apenas um preset genérico de TTS;
- ainda humana/natural o bastante para não soar metálica, robótica ou vocoder pesada.

A meta perceptiva da assinatura é aproximadamente:

```text
voz humana/natural dominante
+
textura fantástica/sintética discreta
```

O efeito deve ser sentido antes de ser identificado como um efeito específico.

---

## 4. VoiceSignature V1

`VoiceSignatureProcessor` é o pós-processamento determinístico do PCM base.

A primeira implementação de produção usa apenas transforms baratos, auditáveis e apropriados ao Galaxy A15:

1. tonal shaping amplo por EQ;
2. leve redução/ajuste de graves;
3. presença conversacional;
4. pequeno shelf de “air”;
5. saturação suave;
6. textura harmônica paralela em quantidade baixa;
7. compressão leve;
8. ganho e limiter.

A assinatura V1 **não afirma** possuir pitch/formant neural ou DSP de alta qualidade quando o runtime não oferece isso. Esses transforms só entram futuramente se preservarem pt-BR, desempenho e identidade no aparelho.

Não usar na identidade normal:

- vocoder forte;
- ring modulation;
- bitcrusher;
- autotune audível;
- chorus evidente;
- reverb permanente assado na identidade;
- pitch extremo;
- textura metálica agressiva.

Reverb/ambiente pertence ao contexto de reprodução, não à pessoa Amora.

---

## 5. Profile de voz servido pelo Core

O tuning da identidade não deve exigir recompilar APK.

O source-of-truth atual é:

`core/amora/voice/amora_voice_profile.json`

O Core expõe o documento validado em:

`GET /v1/voice/profile`

Propriedades:

- autenticado;
- versionado por `schema_version`, `profile_id` e `revision`;
- `ETag` para revalidação barata;
- carregado do arquivo a cada request, permitindo hot tuning após atualização do source;
- validação fail-closed;
- speaker/backend/idioma canônicos não podem ser trocados pelo profile.

O Android mantém o último profile válido em cache. Se Core/profile estiver indisponível ou inválido, continua usando o último profile válido; se nunca houve profile remoto válido, usa o default seguro empacotado no APK.

O app revalida periodicamente o profile. Assim, depois de uma build estrutural instalada, tuning de EQ/textura/dynamics/profiles pode seguir por atualização do Core sem novo APK.

---

## 6. Expressão: base vs cérebro

A assinatura base e a emoção são camadas diferentes.

```text
AMORA base identity
        ↓
VoiceSignature fixa/versionada
        ↓
SpeechStyle produzido pelo cérebro
(rate / pitch-signal / volume / valence / arousal)
        ↓
expressão bounded
```

O Emotion Engine continua sendo autoridade do estado emocional. O diálogo já produz `SpeechStyle` com `rate`, `pitch`, `volume`, `valence` e `arousal`.

A implementação Android consome esses sinais sem permitir que emoção troque speaker ou destrua a identidade.

Importante: no backend Supertonic atualmente integrado via sherpa-onnx, a API usada expõe speaker, speed, silence scale, steps e idioma; **não existe nesta integração um controle nativo de emoção explicitamente comprovado**. Portanto não fingimos suporte a “happy/angry” do modelo. A emoção atual entra via planejamento do Core + transforms bounded suportados.

Se um backend futuro expuser emoção nativa real, o adapter poderá mapear o Emotion Engine para ela atrás do mesmo contrato, após teste físico.

---

## 7. Evitar double-count de emoção

O Core já transforma humor em prosódia base. Hoje:

- arousal influencia `SpeechStyle.rate`;
- valence influencia o sinal de `SpeechStyle.pitch`;
- valence/arousal seguem anexados ao utterance.

Por isso `amora_voice_profile.json` usa `arousal_rate_amount = 0.0` por padrão. O Android não reaplica uma segunda grande alteração de velocidade.

No cliente, valence/arousal podem modular discretamente cor/air/presença dentro de limites pequenos.

---

## 8. Perfis de expressão

A identidade normal é `NATURAL`. Outros profiles são modificadores de expressão, não pessoas diferentes:

```text
NATURAL -> voz normal canônica
SOFT    -> mais macia/aérea
SHY     -> mais contida/suave
SYNTH   -> textura fantástica mais perceptível
CHIBI   -> mais viva/brilhante, ainda reconhecível como Amora
```

O profile ativo pode ser alterado no arquivo do Core durante calibração física sem rebuild do APK.

Depois da calibração, estados emocionais do cérebro devem modular a base de forma contínua; profiles explícitos continuam úteis para modos assumidos/fantasias e para tuning controlado.

---

## 9. Caminho offline e privacidade

Caminho normal:

```text
Core local -> utterance + SpeechStyle
Android -> Supertonic local -> VoiceSignature -> AudioTrack
```

O Core remoto/Gemini TTS não é necessário para fala normal. Falta de quota/API externa não muda a identidade vocal local.

O pequeno profile recebido pelo Android contém apenas parâmetros de voz/configuração, não áudio do usuário nem modelo neural remoto.

---

## 10. Performance

A VoiceSignature foi projetada para O(n) sobre o PCM e sem inferência neural adicional:

- três biquads;
- saturação/textura harmônica simples;
- compressor envelope;
- limiter.

O custo pesado continua sendo a síntese Supertonic, já fisicamente exercitada no Galaxy A15 pelo Voice Lab.

A certificação final deve medir novamente o pipeline completo com VoiceSignature ativa.

---

## 11. Gate físico final

A seleção do speaker está concluída, mas a assinatura completa ainda precisa de aceitação auditiva no Galaxy A15.

Verificar:

- speaker 0 continua reconhecível e natural após DSP;
- assinatura é perceptível sem soar metálica/robótica;
- `NATURAL`, `SOFT`, `SHY`, `SYNTH`, `CHIBI` ficam distinguíveis sem trocar identidade;
- emoção real do Core altera expressão dentro de limites naturais;
- pronúncia de Amora/BK/gírias/frases difíceis;
- artefatos do modelo não pioram com o DSP;
- RTF/latência/RAM/CPU/temperatura;
- conversa longa;
- barge-in;
- restart;
- funcionamento offline;
- update do voice profile sem recompilar APK;
- profile inválido não substitui o último profile válido.

CI não substitui esse gate.

---

## 12. Portabilidade futura

A assinatura é descrita por parâmetros e transforms simples para facilitar uma implementação equivalente no Web/PWA.

```text
Android Supertonic ─┐
                    ├─ amora-voice-v1
Web/PWA backend ────┘
```

A plataforma pode usar backend diferente se necessário, mas deve perseguir a mesma identidade perceptiva e respeitar o mesmo profile versionado.

---

## 13. Estado atual

- Supertonic 3 INT8 speaker 0: **selecionado fisicamente por BK**;
- runtime Android de produção na branch de certificação: **migrado para speaker 0**;
- VoiceSignature V1: **implementada**;
- Emotion/SpeechStyle -> Android: **integrado**;
- profile hot-reloadado do Core + cache Android: **implementado**;
- tuning auditivo e certificação do APK final: **pendentes**;
- Faber permanece apenas como legado de rollout/lab até a nova build ser aceita fisicamente e sua remoção de assets ser segura.
