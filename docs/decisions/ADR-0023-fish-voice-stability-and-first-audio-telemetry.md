# ADR-0023 — Estabilidade Fish e telemetria até o primeiro áudio

Status: ACCEPTED por solicitação explícita de BK.
Data: 2026-10-31.
Refina: ADR-0021 e ADR-0022-turn-level-fish-pcm-streaming-and-expression-tags.
Superseded by: none.

## Contexto / problema

Depois da migração da Web para uma única entrega vocal por turno com PCM incremental,
o caminho físico ficou muito melhor e a latência cognitiva passou a ser aceitável.
Ainda restaram dois sintomas observados por BK:

- o clone Fish às vezes soa muito natural e às vezes ganha caráter mais robótico;
- existe atraso perceptível entre a resposta textual/canônica estar pronta e a voz
  efetivamente começar a tocar.

O transporte atual já evita a espera pelo WAV completo: Fish envia PCM enquanto
continua sintetizando e Safari toca com buffer pequeno. Portanto voltar a quebrar a
resposta pronta em duas ou mais requisições TTS recriaria pontos de falha, gaps e
reinício de prosódia que ADR-0022 removeu.

A revisão também encontrou uma inconsistência concreta: o caminho PCM normal e o
WAV de recuperação montavam payloads Fish diferentes. O stream explicitava
`normalize` e prosódia bounded, enquanto o WAV não reutilizava exatamente a mesma
política. Isso podia fazer uma recuperação de transporte soar diferente mesmo usando
o mesmo clone/modelo.

## Evidência externa confirmada

O SDK oficial atual da Fish documenta para TTS:

- `chunk_length` entre 100 e 300, default 200;
- valores menores favorecem resposta inicial e valores maiores favorecem qualidade;
- `latency` aceita `normal` e `balanced`, com `balanced` como default mais rápido;
- `normalize=true` por default;
- `top_p` default 0.7;
- `temperature` default 0.7, com valores menores produzindo geração mais consistente;
- prosódia bounded por `speed` e `volume`.

A implementação continua usando `s2.1-pro-free`; provider continua capability
substituível e nenhuma opção paga é ativada.

## Alternativas consideradas

1. dividir a resposta final em duas requisições Fish;
2. reduzir `chunk_length` para priorizar TTFA;
3. trocar `balanced` por `normal` para buscar qualidade;
4. aplicar DSP local sobre o clone cloud;
5. migrar imediatamente para WebSocket e falar a partir do preview cognitivo;
6. manter o transporte aprovado e reduzir apenas fontes controláveis de variação,
   corrigindo payloads e medindo primeiro áudio.

## Decisão

### 1. Não resegmentar o turno para TTS

`WEB_SAFARI` continua com uma `SpeechUtterance` por turno cognitivo. Não criar duas
chamadas Fish apenas para tentar começar alguns milissegundos antes.

WebSocket/streaming de texto antes do plano completo continua uma possibilidade
futura, mas somente quando houver uma unidade incremental validada que seja segura
para falar. Preview textual efêmero não pode virar fala irreversível.

### 2. Preservar parâmetros de transporte aprovados

Continuar:

```text
model=s2.1-pro-free
latency=balanced
chunk_length=200
format=pcm no caminho normal
sample_rate=44100
normalize=true
sem DSP cloud
```

Não reduzir `chunk_length` sem evidência física, porque a própria Fish documenta o
trade-off velocidade inicial ↔ qualidade.

### 3. Sampling mais conservador

Fixar explicitamente:

```text
temperature=0.55
top_p=0.70
```

Objetivo: reduzir variação take-a-take sem transformar a voz em leitura monótona.
Esses valores são renderer policy, não estado emocional nem identidade canônica.

### 4. Expressão Fish bounded

A emoção continua pertencendo ao Core. O renderer apenas traduz estado já resolvido.

A tag automática de emoção passa a exigir:

```text
source_intensity >= 0.50
```

No máximo duas tags provider-specific podem ser prefixadas em uma utterance. A ordem
permanece conservadora: emoção canônica elegível primeiro, depois cues/nonverbals já
detectados pelo Core, com dedupe.

Isso reduz over-direction sem remover expressão explícita como risada/suspiro.

### 5. Um único payload policy para PCM e WAV

Stream PCM e render WAV de recuperação devem compartilhar a mesma construção de
payload Fish para:

- texto/tag policy;
- reference_id;
- latency;
- chunk_length;
- normalize;
- temperature/top_p;
- prosódia bounded.

A única diferença estrutural necessária é formato/sample rate.

### 6. Telemetria de primeiro áudio

O backend registra apenas tempos bounded, sem texto, credenciais ou reference ID:

```text
fish_headers_ms
fish_first_byte_ms
fish_first_pcm_ms
```

Esses valores ficam em `voice` dentro de `/v1/web/state` e servem para separar:

- espera de conexão/headers;
- espera do provider até primeiro byte;
- buffering Core até primeiro PCM entregável.

A medição física Safari continua sendo gate separado; CI não certifica áudio real.

## Motivos

- corrige inconsistência real entre stream e fallback sem trocar arquitetura;
- reduz variabilidade de sampling de forma pequena e reversível;
- reduz chance de tags excessivas dominarem o clone;
- mantém a voz aprovada e o transporte já fisicamente melhorado;
- evita reintroduzir múltiplas requisições TTS por turno;
- cria evidência para o próximo ajuste em vez de otimizar latência por palpite;
- preserva privacidade e segredo do provider.

## Consequências

- algumas emoções de baixa intensidade deixam de gerar tag textual Fish, mas continuam
  existindo no Core e podem afetar outros parâmetros/comportamento;
- a voz pode ficar um pouco mais consistente e ligeiramente menos variável;
- o WAV de recuperação passa a respeitar exatamente a mesma política de qualidade do
  stream normal;
- timings são do Core/provider e não substituem medição de playback físico no Safari;
- nenhum banco/schema persistente muda;
- nenhum provider novo é introduzido.

## Critério para revisitar

Revisitar se:

- teste físico mostrar perda clara de expressividade com `temperature=0.55`;
- artefatos robóticos continuarem sem correlação com tags/sampling;
- `fish_first_byte_ms` ou `fish_first_pcm_ms` explicar parcela relevante do atraso;
- o atraso estiver majoritariamente antes da criação/claim da utterance;
- Fish mudar contrato/modelo/free tier;
- existir texto incremental validado suficiente para justificar Fish WebSocket sem
  falar preview reversível.
