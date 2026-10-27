# OmniVoice oficial — laboratório Lightning Free CPU

Estado: implementação experimental; **publicação bloqueada pelo gate de
dependências** descrito em [OMNIVOICE_DEPENDENCIES.md](../engineering/security/OMNIVOICE_DEPENDENCIES.md).
Não houve deploy, benchmark do modelo, aprovação de referência ou validação
Lightning/Safari. Os passos locais abaixo preparam o experimento; a seção de
porta pública só pode prosseguir após resolver esse gate. Não ignorar CVEs.

ZERO_COST_REQUIRED=true. Usar exclusivamente Studio CPU gratuito existente,
4 CPU, sem cartão/GPU paga/auto-upgrade/créditos que virem cobrança. Confirmar
plano, memória, disco persistente e expiração na conta real; não presumir que
limites comerciais sejam permanentes. Se indisponível, parar voz; texto continua.
Não há SDK Lightning, criação de recursos, deploy automático ou fallback pago.

## 1. Clone e ambiente CPU isolado

Linux x86_64 glibc / Python 3.12. O lock experimental é específico dessa
plataforma. Não instalar no Core Alpine/musl ou em GPU. Dentro do Studio gratuito:

```bash
git clone --branch feat/speech-io-v2-omnivoice https://github.com/Sllay/Amora-Chibi.git
cd Amora-Chibi
git rev-parse HEAD
umask 077
export AMORA_VOICE_DATA=/teamspace/studios/this_studio/amora-voice-assets
mkdir -p "$AMORA_VOICE_DATA"
python3.12 -m venv "$AMORA_VOICE_DATA/venv"
source "$AMORA_VOICE_DATA/venv/bin/activate"
python -m pip install 'pip==26.2.2'
python -m pip install --require-hashes -r deploy/omnivoice-worker.lock
export PYTHONPATH="$PWD/core"
python -m pip check
python -c 'import torch,omnivoice; assert torch.version.cuda is None; print("CPU import OK; model not loaded")'
```

Após merge usar SHA revisado de main, registrando-o no relatório do laboratório.
O caminho acima precisa ser o workspace persistente da máquina real: verificar
que sobrevive a restart. Não provisionar volume pago se não houver persistência
gratuita. venv/cache/assets ficam fora do Git. Não copiar env/secrets do Core.
O pacote oficial também declara dependências de demo/training; não servimos
Gradio, não removemos requirements silenciosamente e não adulteramos metadados.

## 2. Gate de dependências (atualmente BLOCKED)

Usar tooling separado para não modificar o ambiente de inferência:

```bash
python3.12 -m venv "$AMORA_VOICE_DATA/audit-tools"
"$AMORA_VOICE_DATA/audit-tools/bin/python" -m pip install 'pip-audit==2.10.1'
"$AMORA_VOICE_DATA/audit-tools/bin/python" tools/security/audit-omnivoice.py --output "$AMORA_VOICE_DATA/dependency-audit.json"
```

Em 2026-10-26: exit 1, 91 pacotes cobertos, 41 advisories/3 pacotes. Não há
exceção ou VEX. Não publicar porta nem configurar Northflank enquanto esse gate
falhar. Experimento privado isolado não equivale a autorização de publicação.

## 3. Download explícito do modelo oficial

Não usar GGUF/INT8/Q4/ONNX quantizado. Download usa arquivos originais do Hub.
Não há download de Whisper. O tokenizer de áudio oficial também é necessário.

```bash
export HF_HOME="$AMORA_VOICE_DATA/huggingface"
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export CUDA_VISIBLE_DEVICES=""
export AMORA_OMNIVOICE_MODEL=k2-fsa/OmniVoice
export AMORA_OMNIVOICE_MODEL_DIRECTORY="$(python -m amora.voice.omnivoice_tools download --path-only)"
printf '%s\n' "$AMORA_OMNIVOICE_MODEL_DIRECTORY" > "$AMORA_VOICE_DATA/model-directory.txt"
export HF_HUB_OFFLINE=1
```

O diretório retornado é o snapshot local resolvido. Para revisão específica,
passar `download --revision <commit-do-Hub> --path-only`, registrando essa revisão.
O loader oficial resolve repo IDs em main; por isso usar o diretório retornado,
não apenas passar revision à API. Manter cache/tokenizer e esse caminho
persistentes. Sem rede na inicialização após download completo. Memória/RAM
necessária e tempo de download ainda precisam ser medidos; não migrar para GPU
ou quantizar se a CPU gratuita não comportar. Degradar voz.

## 4. Upload de referência e criação do prompt

Ainda não existe referência final aprovada por BK. Não há WAV/prompt canônico
incluído. Upload pelo gerenciador de arquivos do Studio de material próprio ou
com direitos/consentimento: reference.wav, mono PCM16, 3–10 s, transcript UTF-8
não vazio em reference.txt. Não usar voz pública sem autorização. Exemplo de
nome de laboratório abaixo não constitui aprovação de identidade.

```bash
chmod 600 "$AMORA_VOICE_DATA/reference.wav" "$AMORA_VOICE_DATA/reference.txt"
export AMORA_OMNIVOICE_REFERENCE_MANIFEST="$AMORA_VOICE_DATA/reference-manifest.json"
export AMORA_OMNIVOICE_PROMPT="$AMORA_VOICE_DATA/voice-clone-prompt.pt"
python -m amora.voice.omnivoice_tools create-prompt --reference "$AMORA_VOICE_DATA/reference.wav" --reference-text-file "$AMORA_VOICE_DATA/reference.txt" --voice-id LAB_REFERENCE_PENDING_BK_V1 --manifest "$AMORA_OMNIVOICE_REFERENCE_MANIFEST" --prompt "$AMORA_OMNIVOICE_PROMPT"
```

Valida WAV/hash/taxa/duração/RMS/silêncio/clipping antes de carregar o modelo.
Chama create_voice_clone_prompt(ref_audio=..., ref_text=...) e prompt.save().
A API oficial pode acrescentar pontuação terminal; manifesto guarda o transcript
exato do prompt. Arquivos novos têm modo 0600; comando recusa sobrescrever assets.
Na inicialização VoiceClonePrompt.load(map_location=cpu) usa weights_only=True.
Hashes, transcript e shape precisam coincidir. Não se reconstrói prompt por request.

Manifesto contém version=1, voice_id, reference_audio, reference_text, sha256,
sample_rate, duration_seconds, prompt_sha256. Mantê-lo privado junto dos assets;
nunca enviar ao Core/browser nem registrar transcript/hashes privados em relatório.
Após restart, recarregar caminho de snapshot salvo e mesmas variáveis/assets.

## 5. Worker privado e probes

O token deve ser um secret exclusivo de serviço, >=32 bytes, diferente de admin
e browser bearer. Injetar como AMORA_VOICE_SERVICE_TOKEN pelo mecanismo de secrets
do host; não escrevê-lo no Git, logs, notebook público ou shell history.

```bash
export AMORA_MODE=cloud
export AMORA_VOICE_RENDERER=omnivoice
export AMORA_ZERO_COST_REQUIRED=true
export AMORA_VOICE_ZERO_COST_CONFIRMED=true
export AMORA_OMNIVOICE_NUM_STEP=32
export AMORA_OMNIVOICE_THREADS=4
export AMORA_VOICE_RENDER_TIMEOUT_SECONDS=60
uvicorn amora.voice.worker:application --factory --host 127.0.0.1 --port 8000 --no-proxy-headers --no-access-log
```

Alternativa após gates de exposição: `PORT=8000 python -m amora.voice.worker`
faz bind em 0.0.0.0, uma instância, logs JSON stdout sem bootstrap Core/Turso.
/live é público e informa só alive. /ready exige service auth e verifica modelo,
prompt, manifesto e identidade, sem sintetizar em probes. Referência inválida
retorna 503; live continua vivo. Falha inicial requer corrigir assets e restart.

Em outro terminal com o mesmo secret injetado, sem imprimir o valor:

```bash
python - <<'PY'
import httpx, os, uuid, pathlib
headers = {'Authorization': 'Bearer ' + os.environ['AMORA_VOICE_SERVICE_TOKEN']}
with httpx.Client(base_url='http://127.0.0.1:8000', timeout=70) as client:
    assert client.get('/live').json() == {'status': 'alive'}
    ready = client.get('/ready', headers=headers)
    ready.raise_for_status()
    assert ready.json()['streaming_capable'] is False
    result = client.post('/v1/render', headers=headers, json={
        'protocol': 'amora.voice.render.v2', 'text': 'Oi, pai. Tô aqui.',
        'cues': [], 'identity': {'request_id': str(uuid.uuid4()),
        'utterance_id': str(uuid.uuid4()), 'turn_id': None}})
    result.raise_for_status()
    from amora.voice.audio_contract import validate_wav
    validate_wav(result.content)
    pathlib.Path(os.environ['AMORA_VOICE_DATA'], 'lab-render.wav').write_bytes(result.content)
print('Authenticated render validated; no secrets printed.')
PY
```

Máximo um render; texto <=800 caracteres, body <=16 KiB e limites ASGI existentes.
Worker timeout 60 s / máximo configurável 80 s, abaixo do limite ASGI 90 s.
Após timeout a função nativa pode continuar: slot fica ocupado, output tardio
é descartado; não enfileirar trabalho ilimitado ou retry automático. SIGTERM tem
grace period limitado; processo travado requer restart explícito do supervisor.

## 6. Benchmark real na CPU gratuita

```bash
python -m amora.voice.omnivoice_tools benchmark --output "$AMORA_VOICE_DATA/benchmark" --repeats 2
```

Executar sem o servidor concorrente. Um modelo/prompt, todos os steps 16/24/32,
corpus público curto/médio/longo com alvos nominais ~1–2/~5/~10–15 s. Durações
reais são medidas, não impostas. JSON/Markdown incluem load modelo/prompt,
primeiro render global, primeiro por caso, renders warm, duração, RTF e peak RSS.
Não incluem transcript da referência, caminho dos assets, prompt ou conteúdo
privado. Escuta/pt-BR/identidade são avaliação separada. Nenhum número real foi
produzido nesta missão; mocks não são benchmark. Não escolher steps por suposição.

## 7. Porta pública e Northflank — somente após gates

Não executados nesta missão. Após dependências aprovadas, referência/benchmark
aceitos e confirmação do plano CPU gratuito, publicar **apenas** porta 8000 pelo
port proxy HTTPS já incluído no Studio gratuito. Não contratar domínio/tunnel/
plano pago. Se indisponível, deixar voz offline. Browser não chama worker direto.
TLS válido, URL pública sem query/auth e sem redirects; egress deve bloquear
IPs privados/rebinding. Não habilitar trust irrestrito em forwarded headers/CORS.
Verificar que o ingress gratuito não exige cookie de login interativo para Core.

Proposta de env do Core, a ser aplicada externamente depois da revisão:

```text
AMORA_VOICE_BACKEND=remote
AMORA_VOICE_WORKER_URL=https://<host-gratuito-HTTPS-do-worker>
AMORA_VOICE_PROTOCOL=amora.voice.render.v2
AMORA_VOICE_EXPECTED_RENDERER=omnivoice
AMORA_VOICE_EXPECTED_MODEL=k2-fsa/OmniVoice
AMORA_VOICE_EXPECTED_VOICE=<voice_id-exato-do-manifesto-revisado>
AMORA_VOICE_TIMEOUT_SECONDS=70
AMORA_VOICE_SERVICE_TOKEN=<secret-exclusivo-do-serviço>
```

Core não recebe referência/prompt/modelo/config de inferência. Somente secret de
serviço e identidade esperada. Mismatch fecha áudio; texto/queue continuam.
Safari já limita fetch vocal a 90 s; não houve alteração de UI. Supertonic V1
permanece configurável explicitamente, congelado, em outro host gratuito.

## 8. Acceptance real

- Gate de dependências e imagem Core aprovado, sem skips/ignores/severity menor.
- Provar zero billing/cartão/GPU, CPU e persistência gratuita na conta real.
- Restart Studio: mesmo snapshot, manifesto/prompt carregados uma vez, /ready.
- /v1/render exige auth; identidade e WAV 24k mono PCM16 verificáveis.
- Medir CPU/RAM/cold/warm/RTF 16/24/32; escutar qualidade sem promover referência.
- Safari: texto antes da voz, mute zero requests, Stop sem áudio tardio, ACK real.
- Worker offline/timeout/mismatch: texto preservado, voz DEGRADED, sem fallback.
- Cancel canônico confirmado + append_event incerto: cancelled/uncertain, sem retry.
- /live e /ready Core/restart continuam saudáveis em Northflank/Turso.

Não implementar STT pesado aqui. Groq permanece; futuro browser STT/WebGPU
poderá enviar transcript à rota Web textual atual, preservando target/consent.
