# ADR-0021 — Fish cloud clone como voz primária da Web

Status: ACCEPTED por solicitação explícita de BK.
Data: 2026-10-30.
Refina: ADR-0002, ADR-0009, ADR-0014, ADR-0019.
Superseded by: none.

## Contexto

O caminho Supertonic 3 + FINAL_B + VoiceSignature provou execução local leve e
continua sendo um artefato importante do projeto, mas a identidade final da voz
passou a existir como uma referência de áudio própria, aprovada por BK:

```text
Amora_Voice_Reference.wav
SHA-256 6752794014bdd0eab6bf4d71ae0d8c97dee798231c970b691ada9552a45602be
```

Experimentos com clonagem local demonstraram o trade-off atual:

- Pocket TTS 24L conseguiu preservar a identidade/naturalidade em amostras boas,
  porém ficou lento demais no Galaxy A15/Python CPU para conversa;
- Pocket 6L foi muito mais rápido, mas não preservou a mesma qualidade;
- OmniVoice foi impraticável no A15;
- voltar a treino/fine-tuning próprio bloquearia outras frentes por dias e não
  garante naturalidade suficiente.

BK criou um clone da referência no Fish Audio e aprovou auditivamente a base no
site do provider. O objetivo imediato é permitir que a Web use essa voz sem
expor credenciais no navegador nem recolocar treino local no caminho crítico.

## Método anterior

A rota Web normal chamava diretamente o backend Supertonic local/remoto:

```text
WEB_SAFARI
→ Core /v1/web/voice/...
→ Supertonic 3
→ 60/40 speaker blend
→ FINAL_B
→ VoiceSignature V1 Soft
→ WAV
→ Safari
```

Gemini não era fallback oculto da voz Web.

## Evidência / problema

O problema agora não é ausência de uma identidade vocal aprovada; a referência
final existe. O bloqueio é reproduzir essa identidade com naturalidade e
latência aceitável em hardware local fraco.

Continuar forçando o Supertonic ou iniciar outro ciclo longo de treinamento
local neste momento atrasaria o restante do produto. A voz Web precisa ser uma
capacidade substituível do Core e não uma dependência do A15.

## Alternativas consideradas

- continuar Supertonic como caminho Web normal;
- treinar Piper/VITS/Matcha single-speaker imediatamente;
- fine-tune + distillation de Pocket TTS;
- rodar Pocket 24L em serviço próprio;
- usar API cloud paga;
- usar clone cloud gratuito com failover e manter backends locais preservados.

## Decisão

Para `WEB_SAFARI`, o caminho normal passa a ser:

```text
Safari
→ Core
→ privacy/remote-processing gate
→ Fish Audio `s2.1-pro-free` + clone persistente da Amora
→ se Fish falhar: Gemini TTS configurado
→ se ambos falharem: VOICE_CLOUD_TTS_UNAVAILABLE
→ texto permanece utilizável
```

### Fish

- é o provider primário de TTS da Web;
- o modelo é **fixado em código** como `s2.1-pro-free`;
- não existe variável de ambiente que possa trocar silenciosamente para
  `s2.1-pro` pago;
- a chave vive no `SecretStore` como `FISH_API_KEY`;
- em hosting, a forma esperada é `AMORA_SECRET_FISH_API_KEY`;
- o clone é selecionado por `AMORA_FISH_REFERENCE_ID` ou pelo secret
  `FISH_REFERENCE_ID`;
- o `reference_id` e a API key não aparecem em `/v1/web/state`, headers ou logs.

### Gemini fallback

Gemini passa a ser fallback explícito de disponibilidade para a voz Web, por
instrução atual de BK. A troca deve ser observável por
`X-Amora-TTS-Provider: gemini` e `X-Amora-TTS-Fallback: gemini`.

Esse fallback é degradação operacional, não redefine a identidade vocal
canônica. Se Gemini não estiver configurado/disponível, a voz falha e o texto
continua.

### Supertonic

Supertonic/FINAL_B/Signature **não são apagados**. Permanecem:

- artefato histórico e fallback local opt-in;
- laboratório/Android quando explicitamente selecionado;
- referência de engenharia para execução offline.

Mas deixam de ser o backend Web default. `AMORA_VOICE_BACKEND` antigo não pode
fazer a Web voltar ao Supertonic por acidente. O opt-in Web passa a usar
`AMORA_WEB_VOICE_BACKEND=local|remote`; o default é `cloud`.

### Privacidade

TTS cloud só é permitido quando:

- `privacy_scope` é `PUBLIC` ou `PERSONAL`; e
- a utterance carrega `remote_tts_allowed=true`.

`PRIVATE`, `AUTH` e `SECRET` não são enviados ao Fish/Gemini.

## Motivos

- preserva a voz que BK já aprovou auditivamente;
- remove compute TTS pesado do A15/Core host;
- mantém a API key fora do navegador;
- evita novo ciclo de treino como bloqueio do produto;
- mantém o provider substituível e observável;
- conserva Supertonic como caminho local explícito;
- permite failover sem travar texto/UI.

## Consequências

- a Web depende de rede para a voz normal enquanto este ADR vigorar;
- Fish/free tier é capacidade substituível, não identidade;
- se o acesso gratuito mudar, a arquitetura deve degradar/falhar, não habilitar
  automaticamente um modelo Fish pago;
- Gemini pode produzir timbre diferente quando acionado; a troca é aceita por BK
  como fallback de disponibilidade e deve permanecer diagnosticável;
- o clone Fish ainda precisa de certificação física no fluxo completo
  Core → Safari, apesar de a base já ter sido aprovada no site do provider;
- treinamento/local TTS continua disponível para pesquisa futura, sem bloquear o
  uso corrente da Amora.

## Critério para revisitar

Revisitar quando:

- `s2.1-pro-free` deixar de ser zero-cost ou ficar indisponível;
- a qualidade/latência Fish no Core → Safari não reproduzir o teste do site;
- um TTS local preservar a mesma identidade com latência prática;
- o fallback Gemini causar degradação inaceitável;
- a política de privacidade exigir voz local em mais escopos;
- houver mudança explícita de BK sobre custo/provider/identidade vocal.
