# Visual Engine V5.0 — RUN LOG

## Estado inicial (25/11/2026)
Planejamento aprovado: 4 RUNs (1+2; 3+4; 5+6; 7 expandida). Nenhuma implementação V5 iniciada. O objetivo mudou de laboratório Canvas manual para runtime MOC3 compartilhado entre live e Companion. V4.5 permanece fallback; PR experimental #370 preservado. Limites 0,2 vCPU / 512 MB. Não criar monitoramento automático.

| RUN | Estado | Evidência |
|---|---|---|
| 01 | PASS (CI de desenvolvimento) | 45829940603 / e65030a |
| 02 | PASS (CI de desenvolvimento) | 45880113324 / 2224aee |
| 03 | PASS (CI de desenvolvimento; voz local) | 46027054526 / ec6572a |
| 04 | PASS (CI); physical/release BLOCKED | 46040683217 / bdcb05d |

## Formato de checkpoint
- RUN / Cxx / data / estado (PLANNED, IN_PROGRESS, BLOCKED, PASS, FAIL).
- Escopo entregue, arquivos e commit/PR.
- Comandos/testes, workflow/job/artefato e métricas observadas.
- Falhas, causa, correções e revalidação.
- Decisões, licenças, limitações e testes físicos pendentes.
- Gate, rollback e próximo checkpoint.

Não preencher PASS sem evidência. Histórico cumulativo; não apagar falhas anteriores.

## RUN 01 — implementação inicial
- Framework oficial 5-r.5 fixado no commit 198a3769c26ca3d7b600e932590433badd392edd; Core oficial família 06 apenas em ambiente de desenvolvimento, hash a medir e fixar; licença de publicação ainda não certificada.
- Modelo exato V4.5: workflow 45534504067, artefato 14986551731, source 333b935d12e7b82397efbc4d797c471e1aec05f1.
- C01–C04: implementação de laboratório WebGL2, referências do modelo, física, motions, expressões, parâmetros, perfis e recuperação; validação CI pendente. Não houve alteração do servidor. Testes físicos adiados por instrução do usuário para RUN 04.

- Primeira CI 45732022065: download exato do modelo e Core, build passaram. Browser falhou ao iniciar com null.length; adicionar stack e fase para identificar causa, sem reduzir gate. Core SHA-256 fixado: 8741f739779b5d5210872bd3d7d99f0f1e56e6c87409e7d26d6bb4b80aa1ef47; MOC3 dd23be7602621ea558b3bca17e2a23ebadb4b5d9b5a08eb73b96169b114e5cc0.

- CI 45732160043 (b815edb): model digest, Core digest, build and textures succeeded; browser failed at first frame in CubismMotion.doUpdateParameters reading null.length. Official pinned framework initializes EyeBlink/LipSync arrays to null and requires setEffectIds. Fix initializes both arrays from model3 Groups (empty arrays when absent), validates declared parameter IDs and calls setEffectIds on every motion before scheduling. Add explicit empty favicon to avoid unrelated browser 404. Revalidation pending; original assertions preserved. Security checks image/dependencies passed; Altare separate failure not certified here.

## RUN 01 — fechamento automatizado
CI 45829940603, e65030a: PASS, 22 drawables, 2 máscaras, 22 parâmetros/texturas, 18 expressões, 8 poses, 3 perfis, 135 frames, CPU p95 1,8 ms na CI, zero erros; evidência 15122236971. Segurança passou; Altare separado falhou. Licença de distribuição e testes físicos pendentes, nenhuma publicação.

## RUN 02 — implementação inicial
- Base exata e65030a. Rotas estáticas de desenvolvimento /live-overlay/ e /companion/, mesmo MOC3 e motor, configuração validada por query, canvas transparente, sem UI no overlay.
- Companion prepara MediaStream local do canvas real, usa PiP padrão ou WebKit por gesto explícito, confirma estado do sistema; ausência de API/MediaStream e suspensão não são mascaradas. Sem vídeo mock, servidor de vídeo, áudio ou permissão de câmera/mic. Renderização em segundo plano depende do browser; sem bypass de throttling.
- Testes automatizados de composição, layout, perfis, identidade, vídeo real e lifecycle. Testes físicos PRISM/Streamlabs/iOS e métricas reais adiados para RUN 04 por instrução do usuário. PRISM documenta iPhone 12+ para edição de cenas ScreenCast; alternativa Streamlabs se fonte web suportada, composição externa em outro dispositivo (não Northflank) ou cliente AVKit nativo futuro. Nenhuma alternativa certificada.
- Referências: https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/captureStream ; https://developer.apple.com/documentation/webkitjs/adding_picture_in_picture_to_your_safari_media_controls ; https://guide.prismlive.com/mobile/guides/features/screencast/using-screencast .
- Gate automatizado pendente; distribuição do Core proprietário não autorizada/certificada; bundle apenas CI temporária.

## RUN 02 — fechamento CI
45880113324 PASS; transparência 29.971 pixels, 35.170 opacos, 0 erro WebGL, vídeo real mudou com olhos, recovery preservou expressão. Artefato 15145993324. PiP físico, PRISM e Streamlabs pendentes.

## RUN 03 — implementação inicial
- Projeção read-only de AvatarSemanticState; protocolo v1 snapshot/state/heartbeat, sessão e sequência, eventos coalescidos e leitura limitada. Credencial visual separada de avatar/admin (1h), subprotocol somente no endpoint exato; sem credencial na URL; origem e limites existentes preservados. Sem texto, IDs de pessoas ou eventos privados no payload.
- Controlador compartilhado nas três superfícies; expressão oficial, gesto, blush e envelope de boca suavizados, idle do rig. Reconexão limitada e preservação do estado. Pareamento explícito; compartilhamento no mesmo perfil por localStorage opcional. Browser source isolado precisa método próprio de pareamento antes da publicação.
- Tap opt-in no player Web existente (WAV/PCM), mesma reprodução, BroadcastChannel local somente amplitude, silêncio/stale/cancel fecham boca. Não transfere áudio nem envelope entre dispositivos ou Discord; não é integração de voz remota universal. Validação física e medição real adiadas. Falta certificar fonte de live isolada, autorização de distribuição do SDK e conexão com voz de outras plataformas; não declarar gate integral concluído até resolver.

- CI 45881626235: 4 testes avatar legados passaram, handshake visual legítimo negado 4401. CI Avatar tests 45881638609: inicialização Web falhou porque inserção dinâmica usou Element.after ausente no harness. Correção candidata: parse estrito do header de subprotocol real e teste de assinatura; botão declarativo no HTML e cleanup unificado no lifecycle existente. Reexecutar gates sem suprimir testes.

- CI 46025921193: assinatura visual validada, handshake ainda 4401; Avatar Tests (push/PR), segurança e contratos de container passaram. Causa identificada no harness: TestClient.websocket_connect muta o dicionário de headers via setdefault; o primeiro caso negativo grava subprotocol vazio e contamina as tentativas seguintes. Corrigir isolamento dos headers em todas as conexões, adicionar negativos de origem ausente/externa e verificar que o fixture não foi mutado. Não alterar política de autenticação para satisfazer teste. Revalidação pendente.

## RUN 03 — fechamento CI
46027054526 PASS: 5 Python, controlador corporal, 15 voz Web, overlay e vídeo real, zero erro WebGL. Evidência 15211919662. Limitações de áudio remoto e fonte isolada ainda abertas.

## RUN 04 — certificação inicial
- Base ec6572a; adiciona wsproto 1.3.2 ao lock e projeto (Uvicorn bare não inclui backend WS); mount isolado /avatar/v5/ somente com AMORA_VISUAL_V5_STAGING_ROOT, preservando V4.5 e avatar legado. Não ativa deploy.
- Revalidação de credencial durante a conexão; configuração explícita ?setup=1 da fonte de live, sem credencial em URL, com pareamento no próprio perfil quando o browser permite interação. Teste real Core/Uvicorn/Chromium, áudio WebAudio de fixture (não TTS real), regressão dos gates anteriores e 15s por perfil, métricas CI. Não equivale a iPhone ou Northflank.
- Bloqueios: Discord worker não tem player de voz identificado; áudio entre dispositivos ainda não implementado; PRISM precisa provar interação e suporte; SDK publicação exige classificação/aceite, não distribuir Core. Nenhuma publicação até aprovação.

- Candidato de relay entre dispositivos: WebSocket de publicação exclusivo para credencial web, opt-in no player, nível numérico 0..1, 10Hz nominal, limite de abuso, um produtor, nenhum PCM/texto/ID, último valor apenas e expiração 350ms. Canal visual recebe eventos audio sequenciados sem consultar DB por amostra. Discord ainda não possui player de voz identificado e não foi alterado. Revalidação pendente.

- Uvicorn wsproto upstream transport stores offers in ASGI subprotocols, not extra_headers; normalize both, reject conflicts and keep verified credential only in request state for expiry/rotation checks. Actual CI uses independent browser contexts for relay (no shared BroadcastChannel); fixture credentials not uploaded. Sources: https://github.com/Kludex/uvicorn/blob/main/uvicorn/protocols/websockets/wsproto_impl.py and https://pypi.org/project/wsproto/1.3.2/.

## RUN 04 — fechamento CI
46040683217 PASS, bdcb05d: 73 Python, 197 JavaScript pass + 1 skipped; real authenticated Core relay across isolated browser contexts; 14.79/30.03/59.98 FPS CI, 15 seconds each, 0 WebGL errors. Evidence 15220540139. Altare remains failing. No physical device, production metrics or license release certification.

## Staging integration
Main 02ce0565 and validated V5 bdcb05d diverged (23 commits behind). Reapply only V5 deltas onto current main; preserve all main changes, merge only additive wsproto changes into overlapping lock and pyproject. New staging candidate must pass full RUN04 again. No deployment, no proprietary vendor bytes in Git. Northflank backup credential is read-only; do not repurpose. Private development preview needs a write-capable authorized deployment path, license-compliant SDK and access control; never expose the current public staging mount to the internet without it.

- Staging CI 46042277927 / 7b4abcc: PASS; Core, Avatar, segurança, containers e Aiven aplicáveis verdes. Preparação de merge: build opcional privado com secrets de SDK e leitura de artefato, verificação do ZIP e manifest, compartilhado com CI; padrão desativado. A licença e a configuração de secrets do host ainda dependem do proprietário. Main pode receber apenas código inativo após novos gates verdes; isso não certifica publicação ou dispositivo.

- Verificação de startup do staging: manifesto obrigatório, SDK/MOC fixados, SHA de cada arquivo, conjunto exato sem symlinks/arquivos inesperados; teste de corrupção. Ajuste de portabilidade Alpine/Docker. Não altera startup padrão sem mount.

## Incident hotfix — 27/11/2026
After merge 96ca3ae, Northflank runtime reported `V5 preview manifest missing or invalid` and restarted. Optional preview startup failure incorrectly propagated into Core. Isolate integrity/configuration failure, log safe reason and fail closed 503 for preview only; keep legacy surfaces and Core health available. No weakened integrity or auth, no DB mutation. New tests reproduce runtime misconfiguration. Deployment still requires valid private bundle and verified image; root unset is immediate rollback.

- Northflank 20:28 UTC: enabled build failed at pinned artifact retrieval after both nonempty secret checks and apk success. Persistent rig recovery: original ZIP digest verified, 45 exact model files preserved in de8a319a022ed52371a3de3be235f312279e5345; no SDK. Replaced expiring authenticated artifact and manual SDK mount with immutable model-only Git reference plus official hosted Core pinned hash; actual enabled Docker build added to CI. Root cause of original artifact fetch (auth, proxy or extraction) not uniquely determined by generic error; dependency removed. No production activation until new candidate passes.

- CI 46064247965 stopped before enabled Docker: 76 passed, two WebSocket TestClient portal teardown cancellations. Test sessions now share lifespan and complete explicit server-driven policy-close handshake before portal exit; audio relay cleanup is asserted on invalid-payload disconnect. Real browser cross-context relay and disconnect coverage retained; no auth/transport bypass. Revalidate.

- PRISM iOS screenshot: Basic authentication challenge not supported by widget. Added bkxd login accepting existing preview password or validated AMORA_DEV_MASTER_KEY (not a Bearer credential). Authenticated same-origin POST generates 12-hour signed path capability; exact overlay assets only, no lab/companion/API access. Isolated browser obtains expiring visual-only credential automatically. No global secret in URL; URL itself is private, may be present in infrastructure access logs, expires after 12h or preview password rotation. Existing opened visual session expires in at most one hour after rotation. No new environment variables. Physical PRISM test pending.
