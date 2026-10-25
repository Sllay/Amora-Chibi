# ADR-0019 — Texto por turno, plano vocal e streaming opcional

Status: ACCEPTED por solicitação explícita de BK.
Data: 2026-10-25.
Refina ADR-0012/0014 (apresentação/delivery) e ADR-0018 (unidade do histórico).
Preserva ADR-0010/0011/0015 e os parâmetros vocais congelados.

## Contexto, método anterior e evidência

O Safari apresentava `SpeechUtterance.text`, já normalizado para TTS, e usava seu
ID como identidade visual. Três segmentos de uma resposta viravam três bolhas,
consumiam três posições do histórico e perdiam risadas/emojis. Stop limpava a
fila local mas notificava somente um ID; um claim/decode tardio podia continuar.
O Core admite uma renderização concorrente, enquanto o Web preparava três.
Limpeza imediata e ausência de mute persistente completavam o problema de UX.

## Alternativas

Concatenar texto vocal no browser perderia o original e exigiria reconstruir a
resposta a partir de ACKs/segmentos. Alterar o provider, instalar outro TTS ou
mover o modelo para Core confundiria apresentação com identidade/infraestrutura.
Uma nova tabela de chat e novo endpoint de streaming não são necessários aqui.

## Decisão

1. Após a validação linguística existente, congelar `SpeechPlan`: `display_text`,
   `speech_text`, cues renderer-neutral, privacy scope e turn ID. Normalização e
   segmentação vocal não reescrevem o texto de apresentação. O provider/validator
   mantém suas regras semânticas e sua política de conteúdo atuais.
2. Entregar o snapshot completo de `display_text` em cada utterance do mesmo
   turno, por extensão aditiva do JSON existente. `text` continua sendo entrada
   vocal. O worker recebe o mesmo payload mínimo de renderização, sem esse
   snapshot, histórico, credenciais de browser ou estado cognitivo adicional.
3. Web usa `cognitive_turn_id` como ID visual/de histórico. Restauração/reclaim
   não cria nova bolha nem concatena o snapshot novamente. Respostas antigas
   sem snapshot são reconstruídas no claim a partir dos display fragments
   persistidos. Histórico antigo sem turn IDs não é agrupado por adivinhação.
4. Manter 50 mensagens user + 50 turnos Amora, total 100, também no DOM.
   Diagnóstico ocupa status separado, não bolhas ou histórico. Texto usa
   `textContent`: emojis, risadas, pontuação e marcadores markdown permitidos
   permanecem literais; nenhum HTML/markdown ativo é acrescentado.
5. LAUGHTER/SIGH/SURPRISE/DISSATISFACTION representam interpretação vocal, não
   novas emoções canônicas. Supertonic não implementa essas cues: todas são
   explicitamente `unsupported`. Marcadores não interpretados permanecem em
   `unsupported_markers`. Não enviar tags hipotéticas nem modificar DSP/style.
6. Mute é preferência local independente, em `amora.web.voice-muted.v1`.
   Texto/claim/histórico continuam; áudio não é solicitado e AudioContext de
   playback não é criado. Captura de microfone explicitamente habilitada ainda
   precisa de seu próprio contexto. Unmute não ressuscita fala interrompida.
7. `cancel` ganha `speech_only=false` por default. O Web usa true para stop/mute/
   erro/unpair/pagehide. Cancela entregas do turno e settle dos segmentos, sem
   sinalizar cancelamento da tarefa cognitiva. O comportamento antigo de
   barge-in por nova fala (inclusive Web) e A15 permanece. Marcador de supressão persiste no JSON das
   entregas canceladas, inclusive para segmentos cognitivos produzidos depois.
   O Web aborta todos os controllers, para player, avança epoch e notifica todos
   os IDs claimados; claims/decodes tardios não iniciam som. Cancelamento falho
   fica diagnosticado e pendente de repetição idempotente; COMMIT não ganha retry.
8. O limite de três claims/look-ahead permanece. Apenas uma preparação WAV por
   vez respeita o slot atual do Core; preparar N+1 pode sobrepor playback N.
9. Limpar abre diálogo modal nativo com descrição explícita de memória intacta,
   foco inicial em Cancelar e ações independentes. Só a confirmação limpa
   histórico/bolhas locais; pairing, mute e estado do Core continuam.

## Streaming: contrato preparado, transporte não ativado

`VoiceBackend.render` permanece obrigatório e inalterado. `StreamingVoiceBackend`
é protocolo opcional com `VoiceSession`, `VoiceChunk` e iterator `stream`.
Chunks identificam sessão/turno/utterance, sequence, bytes PCM16 little endian,
sample rate, canais e final. Validador rejeita descontinuidade, mudança de formato
e stream incompleto; fechar o iterator fecha o producer. Não há rota streaming,
renderer novo ou declaração de capacidade streaming nos backends atuais.

O caminho WAV completo está isolado em `WavPlayer`. O futuro sink
`PcmStreamPlayer` aceita Float32 PCM interleaved incrementalmente, copiado,
com identidade/ordem/formato validados, buffer temporal bounded, backpressure,
underrun observável, final após consumo e stop por epoch. Agenda pequenos
AudioBufferSourceNodes no clock do AudioContext, sem decode de arquivo completo.
Uma futura bridge deve converter PCM16 para Float32 e respeitar backpressure;
um AudioWorklet/ring buffer pode substituir esse sink sem mudar o plano vocal.
Status de buffer/agendamento não autoriza ACK falado. O Web atual continua
started no início do player e ACK após onended real, nunca após render/download.

## Fontes técnicas e limites

- [Web Audio, start/stop e AudioBufferSourceNode](https://github.com/WebAudio/web-audio-api/blob/main/index.bs):
  `start(when)` usa o mesmo clock de `currentTime`; stop anterior ao horário
  agendado impede reprodução. Consultado em 2026-10-25.
- [BCD: AudioBufferSourceNode](https://github.com/mdn/browser-compat-data/blob/main/api/AudioBufferSourceNode.json):
  start em Safari desde 7.
- [BCD: HTMLDialogElement](https://github.com/mdn/browser-compat-data/blob/main/api/HTMLDialogElement.json):
  showModal em Safari desde 15.4, consultado na mesma data.

Compatibilidade de API não certifica timing/áudio físico. WAV atual e PCM
sintético são caminhos distintos nos testes; worker Supertonic real e Safari
continuam gates físicos. Nenhum OmniVoice/modelo foi instalado no Core.

## Consequências e reavaliação

O snapshot aumenta JSON por segmento, bounded pela realização validada atual.
Não há migration SQL, nova origem, nova permissão, secret ou alteração de host.
Falha de voz preserva todo o texto, inclusive depois de stop/reload. IDs visuais
independem de leases de áudio. Reavaliar com realização textual incremental,
novos renderers/cues, múltiplos devices por turno ou streaming remoto real.
Não afirmar suporte acústico a cues antes de implementar e certificar um mapper.
