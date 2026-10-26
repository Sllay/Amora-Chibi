# Review — Speech I/O V2 / OmniVoice CPU / cancel audit boundary

Base main e2afcde5cdf613eae1d73bd9e53ffc6c570c67cc; branch
feat/speech-io-v2-omnivoice. Data: 2026-10-26. Nenhum deploy/gasto iniciado.
**Implementação testada; publicação do worker BLOCKED por dependências.**

## Arquitetura / protocolo / assets

Antes: RemoteVoiceBackend V1 e endpoint worker acoplados ao Supertonic.
Depois: Core cliente HTTPS V1/V2, worker renderer-neutral, seleção exclusivamente
server-side. [ADR-0021](../../decisions/ADR-0021-renderer-neutral-speech-io-and-zero-cost-omnivoice-lab.md)
registra payload/headers, identidade esperada, privacidade, cues e limites WAV.
Northflank não instala/importa/carrega OmniVoice; browser não escolhe renderer.

OmniVoice oficial 0.2.1 / k2-fsa/OmniVoice, CPU float32 incluindo tokenizer;
modelo/prompt carregados uma vez. Sem quantização, GPU, Whisper ou fake streaming.
Manifesto/prompt privados 0600, hash/transcript/shape/taxa/duração/RMS validados;
probes não sintetizam. Não há referência aprovada incluída. Supertonic/FINAL_B/
Signature/DSP permanecem frozen. Cues oficiais só no renderer; unsupported é
reportado nos headers e estado. Timeout retém slot até native call terminar.

ZERO_COST_REQUIRED=true, R$ 0,00 obrigatório. Lightning Free CPU Studio / 4 CPU
é laboratório substituível; sem cartão, cobrança, SDK de deploy/billing, fallback
pago ou seleção de GPU. Indisponibilidade gratuita degrada voz e preserva texto.
[Runbook](../../deploy/LIGHTNING_OMNIVOICE.md): clone/install/download/upload,
prompt creation/start/probes/render/benchmark/public port/Core env/acceptance.

## Cancel/event — causa e correção

Staging: POST /v1/conversation/utterances/{id}/cancel, append_event,
SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN / RemoteProtocolError, correlation
729223f6-ea9d-41bc-9a83-569d4b4ea836. Recovery generation=1 já provado. O evento
posterior à mutação canônica estava transformando cancelamento confirmado em 500.
Não houve alteração do adapter de storage nem novo diagnóstico de recovery.

Somente após mutação/settle confirmados, OUTCOME_UNKNOWN especificamente em
AmoraDatabase.append_event de conversation.utterance_cancelled retorna cancelled
com metadata cancel_event_persistence=uncertain. Log seguro/correlation ID da
request, contador process-local, DEGRADED/fingerprint. Sem replay de handlers,
retry de INSERT/COMMIT ou registro falso. Outros erros continuam fail-closed.

Quatro regressões SQL-backed: resposta perdida no INSERT do evento ou COMMIT
mantém HTTP 200/cancelled/texto, exatamente um INSERT INTO events, zero replay.
Consulta independente recupera Event Store. Falha no UPDATE ou COMMIT da
mutação principal mantém HTTP 500. Logs não contêm SQL, conversa ou credenciais.

## Arquivos / ownership

- Cancel: conversation/service.py, integrated.py, routes.py; app.py, operations.py;
  test_cancel_event_boundary.py. Sem mudança de semântica do ACK/Stop cognitivo.
- Transporte: voice/contract.py, identity.py, audio_contract.py, remote.py,
  worker.py/renderers.py; web/routes.py apenas adapter/headers, sem UI.
- Worker: omnivoice_renderer.py, reference.py, omnivoice_tools.py; lock/input
  opcionais em deploy; tools/security/audit-omnivoice.py; CI dependency gate.
- Testes: test_omnivoice_renderer/tools/dependency_gate.py, test_voice_protocol_v2.py.
- ADR/canon Architecture/runbook/security inventory/PROJECT_STATE/este review.

Sem alterações em UI, cognition/providers, schema/storage/Turso, STT Groq,
parâmetros vocais/DSP, tokens existentes, pairing ou recursos/config de hosts.

## Gates executados

- `.venv/bin/python -m pytest -o addopts= tests -q`: **537 PASS**, cinco avisos
  existentes, zero skips. Baseline 452; 85 regressões novas.
- Foco (remote_storage, remote_connection_recovery, cancel_event_boundary,
  omnivoice_renderer/tools/dependency_gate, voice_protocol_v2, remote_voice,
  chat_speech_plan, usage_recovery_stt, web_safari_v101/device_routing,
  pairing/pairing_api, security_predeploy, cloud_runtime): **241 PASS**.
- Node 22.23.4 `--test avatar/tests/*.test.mjs tests/web_*.test.mjs`: **50 PASS**,
  inclusive mute, Stop 3 claims, turn bubbles e texto preservado após falha TTS.
- Runtime build sha256:7b5a187cb11f7584695747792dfb6864b90aaa0bcb5d3a2ee3b390874ff3a4f7,
  69,945,832 bytes; ausência torch/torchaudio/omnivoice/sherpa_onnx verificada.
- `python3 tools/cloud/smoke-container.py amora-core:speech-io-v2`: PASS non-root,
  read-only, cap-drop ALL, NNP, probes, Web/Avatar, auth/pairing, texto sem voz,
  SQLite, restart e SIGTERM.
- Validation Alpine sha256:6d667889124e773a0fb100a9e18e96d2de2d11da6cf037e4ed3f052404481682:
  **537 PASS** com network-none/read-only/non-root/cap-drop ALL/NNP/tmpfs 256 MiB.
  Runtime pip check PASS; Ruff voice/testes novos/audit e diff check PASS.
- Venv glibc separado: install 91 hashes, import oficial OmniVoice 0.2.1,
  torch/torchaudio 2.11.0+cpu, CUDA=None, pip check PASS. Não carregou modelo.
- Audit opcional: **FAIL correto**, 41 advisories, 91 cobertos sem skips. CI
  bloqueia, sem ignore/VEX. [Evidências](../security/OMNIVOICE_DEPENDENCIES.md).
- Trivy local inconclusivo: DB mirror.gcr.io Forbidden e token GHCR DENIED.
  Sem skip-db-update/base desatualizada para declarar PASS. Gate HIGH/CRITICAL
  incluindo unfixed segue na CI sem alteração. CI/PR/SHA finais no handoff.

## Pendências reais

Dependências corrigidas/aprovadas antes de exposição; referência BK; benchmark
full-quality em quatro CPUs gratuitas (load/cold/warm/duração/RTF/RSS 16/24/32);
persistência/HTTPS/ingress/quota real Lightning; áudio/Stop/mute Safari/Northflank;
cancelamento real no Turso. Nenhum benchmark real ou certificação física nesta
missão. Testes mockados não encerram esses gates. Nenhum fallback pago/oculto.
