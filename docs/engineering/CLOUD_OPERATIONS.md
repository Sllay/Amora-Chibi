# Core/Web Linux — operação e gates

ADR-0015 define a direção. Imagem: `docker build -t amora-core .`.
A imagem não inclui modelos TTS, banco, vault, tokens ou arquivos de runtime.
`deploy/python.lock` fixa as dependências validadas; o Python base está
pinado por digest e o build backend setuptools/wheel por versão. Não publicar imagem
com CA/proxy de desenvolvimento incorporados.

Modo local: `AMORA_HOME=/caminho/runtime amora serve`; bind padrão 127.0.0.1.
Modo cloud: `AMORA_MODE=cloud`, `AMORA_HOST=0.0.0.0`, `PORT=8765` (ou porta
injetada pelo host). `--host/--port` também são aceitos.

Exigir `AMORA_SECRET_LOCAL_API_TOKEN` estável, fornecido por secrets do host;
não imprimir o valor. Providers usam AMORA_SECRET_GROQ_API_KEY etc. Não colocar
secrets no Dockerfile, build args, imagem, Git ou URL. Sem providers, texto local
continua funcional. Expor TLS via ingress; não abrir porta de banco/worker ao usuário.

SQLite cloud requer volume em AMORA_HOME e declaração
`AMORA_DURABLE_FILESYSTEM=true`. Uma variável não cria um volume: configurá-lo
no host e testar restart/restore. O modo cloud recusa SQLite efêmero. SQL remoto
sozinho também não resolve assets binários. Nunca escalar horizontalmente antes
de distribuir coordenação social, jobs e pareamento.

Uma réplica, um processo, sem Uvicorn --workers. `/live` verifica processo;
`/ready` verifica schema e escrita transacional; `/health` verifica integridade
do banco e retorna 503 em falha. Voz é capability opcional, não gate de readiness
do chat. Endpoints administrativos continuam Bearer-authenticated. Pairing CLI
precisa executar na mesma máquina/volume; não expor emissão de código publicamente.

SIGTERM usa graceful shutdown Uvicorn (30s), lifespan fecha console/stores;
chamadas nativas em thread não garantem preempção. Scheduler continua opt-in,
sem ativar nova autonomia. Logs cloud em stdout JSON e arquivo rotativo local;
coletar stdout no host para retenção durável. Cache/modelos não são memória.

Koyeb é alvo de configuração, não dependência em código. Configurar porta,
health check `/ready`, secrets, volume quando necessário e escala mínima/máxima
conforme orçamento aprovado por BK. Nenhuma conta, billing ou deploy é criado
por estes arquivos. Certificação remota continua pendente.

Build validado neste ambiente com proxy de sessão: `--add-host proxy:<IP do
proxy>`, build args HTTP_PROXY/HTTPS_PROXY herdados e BuildKit
`--secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt`. A CA é montada
somente no RUN; não fica na imagem. Não desabilitar TLS. O nome/IP pertence
ao ambiente de build, não à configuração portátil. Container non-root passou
/ready, shells Web/Avatar, SIGTERM exit 0 e restart com volume Docker.

## Storage remoto e assets

Configuração: `AMORA_STORAGE_BACKEND=libsql`, `AMORA_LIBSQL_URL=libsql://host`
(ou HTTPS origin), `AMORA_SECRET_LIBSQL_AUTH_TOKEN` injetado e
`AMORA_ASSET_STORAGE=database`. Sem volume, esta combinação é obrigatória.
SQLite continua default; não há fallback silencioso quando libSQL falha.
Backend remoto implementa Hrana HTTP v2 stateful (`/v2/pipeline`, baton),
BEGIN IMMEDIATE, constraints e mapeamento de tipos incluindo BLOB. URLs com
credenciais/query e redirects cross-origin são recusados. Writes/commit com
resultado ambíguo não são repetidos automaticamente. Reinicie o Core após
falha de transporte que invalide a conexão; readiness acusa indisponibilidade.

Migration 11 é aditiva: `avatar_asset_blobs`. Não move arquivos automaticamente.
Para migrar estado existente: parar escritores, criar `amora backup`, preservar
.db e .avatar.zip fora do host, configurar destino remoto **vazio**, executar
`.venv/bin/python tools/cloud/restore-storage.py snapshot.db --empty-destination`
e validar o novo Core antes de apontar clientes. Import verifica integridade,
constraints, SHA dos assets e recusa destino não vazio; schema 10 é atualizado
na transação de import. Conservar origem/backup para rollback; não apagar dados.

`amora backup` remoto faz snapshot lógico consistente sob transação, cria SQLite
portátil, recria FTS sem copiar shadow tables e emite sidecar de assets. Testado
com mock Hrana + SQLite real, não conta Turso. Export não é streaming: medir
volume, timeout e duração do lock de escrita antes de mover base grande.
Sprites SQL são adequados a objetos pequenos; respeitar limite de payload do
serviço e orçamento. Um BlobStore futuro pode usar object storage. Nunca
considerar cache efêmero como backup; copiar snapshot/sidecar para destino
externo com política de retenção definida por BK.

SQL remoto síncrono preserva interfaces existentes; latência por query e bloqueio
de endpoints async são riscos medidos em staging antes de uso contínuo.
Semântica de dedupe não é exactly-once dos handlers. Scheduler e pairing seguem
locais à única réplica. Não ativar múltiplas réplicas com estes limites.

Referência de protocolo auditada: repositório oficial tursodatabase/libsql,
`docs/HTTP_V2_SPEC.md`, consultado em 2026-10-23. O SDK HTTP Python legado não
oferece transações; por isso não foi usado como substituição DBAPI stateless.

## Worker de voz independente

Core: `AMORA_VOICE_BACKEND=remote`, `AMORA_VOICE_WORKER_URL=https://<origin>`,
`AMORA_VOICE_SERVICE_TOKEN` distinto do token do usuário. Local permanece default.
Não há fallback para outra voz. Timeout e resposta bounded (16 MiB), WAV PCM16
mono 24 kHz e model/voice IDs são validados. Abort/cancel preserva fila/ACK;
compute nativo/Modal já iniciado pode terminar, mas o Core rejeita resultado
se a utterance foi cancelada. Render não equivale a spoken.

Worker ASGI genérico: `uvicorn amora.voice.worker:application --factory`.
Injetar somente AMORA_VOICE_SERVICE_TOKEN e paths de modelos/lib. Não injetar
secrets de cognição, token de usuário, Turso ou banco cognitivo. Nenhum bootstrap
do Core é executado. `/ready` autenticado carrega engine e biblioteca reais;
file presence não conta como readiness. Worker recebe texto já autorizado e
expressão numérica bounded, sem histórico, relações, metadata ou estado canônico.
Texto pessoal enviado ao worker integra a fronteira de processamento autorizada
por esta migração; AUTH/SECRET são sempre barrados no Core. Não registrar bodies.

`tools/cloud/prepare-voice-bundle.sh` produz bundle separado usando archive
Supertonic revisado por SHA-256, WORLD no commit canônico, builder style 60/40
existente e o mesmo C++ FINAL_B/Signature. Requer venv com Core, compilador Linux,
archive+checksum revisados e checkout WORLD limpo. Nada é baixado implicitamente
ou aceito com verificação desabilitada. Manifest de arquivos é verificado antes
de empacotar/carregar. Rever licença/provenance junto dos hashes; manifest
autogerado não substitui revisão da origem.

Modal: instalar CLI `modal==1.6.2` separadamente do Core, definir
AMORA_VOICE_BUNDLE, criar secret `amora-voice-service` contendo somente
AMORA_VOICE_SERVICE_TOKEN e executar explicitamente
`modal deploy deploy/modal/voice_worker.py`. Arquivo limita a uma instância,
CPU e memória; não garante custo zero. Revisar orçamento/quota antes do comando.
SDK/decorators foram conferidos no pacote oficial Modal 1.6.2; implantação
remota não executada. Bundle real, warm/cold latency, acústica, Safari físico,
licença e monitoramento continuam gates. Tickets de download direto são futuros;
não foram adicionados para preservar a integração conservadora via Core.

## Living Console / Dev MCP foundations

`GET /v1/operations/state` exige o mesmo Bearer de administração. Schema
`amora.operations.v1` projeta intenção atual, DialogueAct validado, estágio de
tarefa, provider/model, quota/cooldown, módulos, fala, jobs, latência e IDs.
Nunca retorna contexto completo, texto de conversa, memória, relações, argumentos
ou resultados textuais de tools, secrets ou chain-of-thought. Resultados de ações
são estados/códigos operacionais; o resultado técnico completo permanece nos
stores/journal e logs existentes autenticados, separado da visão resumida.

Erros se agrupam por módulo+código em fingerprint estável, com contagem e
primeira/última ocorrência. Cada request recebe X-Correlation-ID; IDs de calls/
plan/turn são preservados onde disponíveis. ActionBus registra requested,
running e succeeded/failed/denied/timed_out. Jobs são apenas snapshot do scheduler
existente; nenhuma nova política autônoma é ativada. Estado agregado é bounded
e local ao processo; após restart, reconstruir detalhes duráveis via stores.
Não há servidor Dev MCP nem console remoto ativo nesta etapa: esta API é a
fundação consumível futura, sem novos poderes administrativos.

WebSocket de eventos continua autenticado e agora exclui SECRET além de AUTH;
disconnect sem novos eventos libera subscription/tasks. Continua sendo stream
bounded sem replay durável. Pareamento usa lock de arquivo/thread Linux para
consumo único entre CLI/threads; ainda não é pareamento distribuído.
