# ADR-0021 — Speech I/O V2 e laboratório OmniVoice sem custo

Status: ACCEPTED por solicitação explícita de BK; OmniVoice EXPERIMENTAL.
Data: 2026-10-26. Base: main e2afcde (PR #63).
Refina ADR-0015/0019; preserva ADR-0010/0011/0020. Não promove nova voz canônica.

## Contexto, método anterior e evidência

VoiceBackend e a fronteira opcional de streaming já existem. O protocolo V1 e
worker conheciam Supertonic/WORLD diretamente. OmniVoice oficial 0.2.1 permite
prompt reutilizável e generate completo; qualidade/velocidade CPU ainda não foram
medidas. Separar renderer de transporte evita redefinir Core, voz e delivery.

Staging confirmou cancelamento canônico seguido de append_event incerto,
correlation 729223f6-ea9d-41bc-9a83-569d4b4ea836. Recovery Hrana funcionou;
o HTTP 500 restante pertence à fronteira entre mutação e evento posterior.

## Alternativas e decisão

Rejeitados: modelo no Northflank, quantização, GPU paga, troca da baseline,
renomear V1 silenciosamente, aceitar identidade descoberta na resposta e fingir
streaming dividindo WAV pronto. Escolhida a fronteira renderer-neutral:

- Core é cliente HTTPS. V1 permanece Supertonic; V2 amora.voice.render.v2 leva
  somente SpeechText, expressão bounded, cues neutras e request/utterance/turn IDs.
  Não leva DisplayText, histórico, relações, memória, prompt cognitivo, bearer do
  browser, Turso ou segredos cognitivos. V2 proíbe model/renderer/reference/tuning.
- WorkerVoiceRenderer possui state/ready/render. A seleção AMORA_VOICE_RENDERER
  ocorre no servidor. Core fixa protocolo/identidade esperada por configuração;
  não escolhe renderer por request. Valida renderer/model/voice/sample-rate,
  streaming e IDs ecoados. V1 incompatível recebe 409, sem troca silenciosa.
- SupertonicRendererAdapter reusa engine e DSP congelados. OmniVoiceRenderer é
  opcional, pacote oficial 0.2.1, k2-fsa/OmniVoice, CPU e torch.float32 inclusive
  no audio tokenizer. Sem autocast, modificação de pesos, quantização ou Whisper.
  num_step=16/24/32 somente servidor; guidance_scale=2.0, class_temperature=0.0.
  Nenhum candidato venceu benchmark. Supertonic continua baseline congelada e
  opção explícita em host gratuito; não é fallback automático.
- O contrato compartilhado valida WAV PCM16 mono 24 kHz: estrutura, alinhamento,
  duração <=120 s, tamanho <=16 MiB, truncamento e clipping >=1%. NaN/Inf ou
  samples fora de [-1,1] são recusados antes de converter float para PCM.
- OmniVoiceReferenceManifest versiona voice_id, WAV/transcript, SHA256, taxa,
  duração e hash de VoiceClonePrompt. Referência de laboratório: 3–10 s, mono,
  PCM16, RMS >=0.003, sem silêncio/clipping patológicos. Não há referência no Git
  ou aprovação automática. Nomes congelados não rotulam clone experimental.
- create-prompt usa API oficial com ref_text, salva prompt/manifest privados.
  O worker carrega modelo/prompt uma vez; probes não sintetizam. Hash, transcript
  ou shape inválido impedem readiness. Arquivos persistem no host; restart
  revalida. Snapshot local pinado + HF_HUB_OFFLINE=1 evita drift no restart.
- Tags verificadas no upstream: LAUGHTER→[laughter], SIGH→[sigh],
  SURPRISE→[surprise-oh], DISSATISFACTION→[dissatisfaction-hnn]. Como o plano não
  fornece offsets, tags precedem o segmento, deduplicadas. Não entram no chat.
  Supertonic reporta cues unsupported; OmniVoice reporta volume/expression
  unsupported, sem inventar equivalência com DSP. Rate usa speed oficial.
- streaming_capable=false. VoiceSession/VoiceChunk/StreamingVoiceBackend e
  PcmStreamPlayer permanecem. Timeout não mata uma função nativa: descarta áudio
  tardio e retém o slot até terminar. Shutdown tem prazo; supervisor pode encerrar.
  Não existe retry automático de render.

## Invariante financeiro absoluto

ZERO_COST_REQUIRED = true: **R$ 0,00 obrigatório**, sem cartão, cobrança
automática, auto-upgrade, GPU paga, pay-as-you-go ou fallback pago. A exigência
atual de BK prevalece sobre a redação histórica de custo desejável. Primeiro
laboratório: Lightning AI Free CPU Studio / 4 CPU. Sem SDK de billing/deploy,
provisionamento ou seleção automática de GPU. O operador declara plano gratuito
com AMORA_VOICE_ZERO_COST_CONFIRMED=true; isso não certifica a conta/billing.
Se o gratuito acabar, voz DEGRADED, texto disponível. Host continua substituível.

## Cancelamento confirmado e evento incerto

Somente após os writes canônicos/settle concluírem, OUTCOME_UNKNOWN
especificamente em AmoraDatabase.append_event de conversation.utterance_cancelled
retorna cancelled com metadata cancel_event_persistence=uncertain, log seguro,
contagem e módulo DEGRADED/fingerprint. Sem novo append, replay de handlers ou
linha falsa. Mutação principal/COMMIT incerto e outros erros continuam fail-closed.
Contagem é bounded no processo; logs são evidência separada após restart.

## Fontes e limites

API/tags/licença Apache-2.0 verificadas no wheel oficial 0.2.1, SHA256
23f113ef51116a16308b55c4c2ac9c08efca7dfb594802f5c8adfb7523313ccc,
[PyPI](https://pypi.org/project/omnivoice/0.2.1/) e
[repositório oficial](https://github.com/k2-fsa/OmniVoice), em 2026-10-26.
Importação CPU real e pip check passaram em venv separado. Não carregamos pesos,
referência aprovada ou modelo real; não fizemos benchmark/Lightning/deploy.
Números de GPU do upstream não são estimativa para quatro CPUs.

**Worker bloqueado para publicação:** auditoria completa do lock experimental
encontrou 41 advisories em Pillow/setuptools/Torch. Gate novo falha sem exclusões;
gate HIGH/CRITICAL da imagem Core intacto. Ver
`../engineering/security/OMNIVOICE_DEPENDENCIES.md`. Não tratar código mockado
verde como aprovação operacional do worker.

## Consequências e reavaliação

Referência final BK, segurança do worker, benchmark CPU, quota gratuita real e
áudio Safari/Northflank são gates separados. STT Groq permanece; futuro STT
browser/WebGPU pode enviar transcript à rota Web existente, com target/consent/
conversation no Core. Nenhum STT pesado novo. Não muda cognition ou personalidade.
Revisitar com patches upstream, referência aprovada, benchmark ou streaming real.
