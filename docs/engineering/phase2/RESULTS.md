# AMORA — Fase 2: correções e evidências

Base: `audit/phase1-core-forensics`, `452a4d9`. O ref Git do PR #166 foi
confirmado, mas a API GraphQL recusou acesso (`Forbidden`), impedindo verificar
estado/checks remotos. Main permaneceu `7504d23`; foram publicadas cinco
branches empilhadas. O relatório original da Fase 1 não foi alterado.

## Correções por bloco

| Bloco | Commit | Causa e correção | Evidência |
|---|---|---|---|
| A | c6ab0c7 | AUTH/SECRET eram redigidos tarde ou só parcialmente. Redação antes de inferência/floor/evento/retorno; extra_payload protegido descartado; messaging bloqueado antes de attention/audit; áudio bloqueado antes de cache/STT; metadata sensível do histórico descartada. | Reproduções F07 e testes AUTH/SECRET separados; 40 adversariais A passaram. |
| A | c6ab0c7 | Relatos/disclosures viravam pedidos de preferência. Novo `language/attribution.py` representa spans, atores/destinos e cláusulas; elegibilidade distingue destinatário e sujeito da preferência. Commit recompila elegibilidade e verifica evidência vinculada a texto/turno/ator/destino/versão e consentimento/escopo. | F01/F03/F02/F02b passam; ataques stale/forged não escrevem, escolha legítima persiste uma vez. |
| B | d35ed25 | Timeout interno NVIDIA caía no catch do caller. Catch tipado anterior separa `NVIDIA_IDLE_TIMEOUT`/`NVIDIA_TOTAL_TIMEOUT`, estágio sanitizado e backoff. Budget curto do caller não penaliza saúde global. Metadata generation leva provider/fallback/reasons à Web. | F06 passa; regressões de provider/budget/SSE: 48 passaram, 5 xfails ainda pertencentes aos blocos seguintes naquela etapa. |
| C | 480af7d + revisão | Budget media apenas contexto e plano ficava stale. Estimativa explícita de envelope, default 8192; retirar projeções opcionais antes de histórico antigo; preservar última troca causal; replanejar e atualizar códigos com planner correto. Overflow irredutível é local e observável, inclusive na reflexão. Guards antes do HTTP das realizações nativas. | F04/F05 passam; 7 testes de contexto, autoria, retenção, orçamento e repetibilidade passaram. |
| C | 480af7d + revisão | ACIR levava literal ao system. Projeção recursiva remove content literal, incluindo listas, conservando papéis e conteúdo semântico estruturado; ConversationTurn conserva texto original. | F10 passa e casos de marcador adversarial/nested ACIR passam. Não se afirma resistência universal a prompt injection. |
| D | 0a57c6a + revisão | `father_of` não era reconhecido; replay exigia stream. Consulta de parentesco por aresta direcional; replay `realize` valida sem usar respond/commit/usage/circuit; deepcopy nos dois caminhos evita contaminação entre candidatos. Rejeição/overflow preservam códigos próprios no replay. | F08/F09 passam; forense, replay e diálogo/emocional: 31 passaram naquela etapa. |
| V3 | b97c180 | Persona existia só no laboratório. Perfil opt-in versionado lê bytes originais e verifica SHA; snapshot congela seleção; protocolo social real recebe V3 e mantém autoridade do Core. Removida instrução legada de “saída somente falável” que competia com JSON nas realizações nativas Groq/OpenRouter/Gemini. | Staging local Web/SSE com transporte NVIDIA simulado; 11 testes V3 passaram. |

As onze assertions forenses foram reproduzidas inicialmente com `--runxfail`
(falhando), corrigidas em blocos e retiradas de xfail após passar. O arquivo
`tests/test_phase1_forensics.py` permanece como regressão com 17 casos.
F04/F05 agora aceitam explicitamente overflow em orçamento impossível de 256,
em vez de exigir perda silenciosa de conteúdo essencial. Não se eliminou a
assertion de orçamento nem se alegou contagem exata de tokens.

## Revisão dos dois agentes e defeitos adicionais

Somente `amora test` e `core especionar` foram reutilizados, sem agentes
aninhados. O principal implementou; os agentes revisaram e criaram testes em
arquivos próprios. A revisão encontrou, reproduziu e levou a correções adicionais:

- Reinjeção de texto protegido por extra_payload e passagem por audit messaging.
- Disclosure com pronome incidental concedendo preferência; pedido após aspas
  perdido; destinatário do relato confundido com autor.
- Contexto crescendo depois da estimativa final; razão/planner desatualizados.
- Literal ACIR dentro de listas; reflexão deixando overflow escapar depois da fala.
- Contexto compartilhado entre replay streams, contaminando o próximo candidato.
- Fixtures de arquivo ausentes da camada Docker de validação (ver gates abaixo).

Atribuição ainda é gramática local conservadora, não parser geral de PT-BR.
Ambiguidade nega escrita de preferência, sem proibir opinião ou expressão adulta.
A estimativa caracteres/4 não é tokenizer NVIDIA; a guarda verifica o envelope
serializado estimado. Groq pode chamar especialista antes de detectar overflow
no surface: o código é `NO_SURFACE_REQUEST`, não “nenhuma chamada remota”.

## Estado efetivo da V3

- **Staging local isolado:** ativada explicitamente por
  `AMORA_SOCIAL_EXPRESSION_PROFILE=amora-v3`. API, autenticação, compilador,
  estado, SSE, fila e validator reais; somente HTTP NVIDIA simulado.
- **Container local:** startup da API e payload opt-in com V3 verificados com
  `--network none`. Presença dos bytes no payload foi comprovada.
- **Staging remoto:** não configurado/deployado nem testado nesta sessão.
- **Produção:** nenhuma alteração de configuração/deploy. Default continua
  `core-v1`; não há afirmação de V3 ativa num host produtivo.
- **Comportamento real:** nenhuma geração remota, avaliação humana ou
  certificação Fase 3 executada. CI não mede humor ou naturalidade.

`state.social_expression_profile` declara configuração/hash esperado, não prova
aderência de modelo. O snapshot e `last_realization.expression_profile` ligam o
perfil ao turno validado. Os testes capturam bytes no request do adapter.

Com V3, uma resenha adulta apropriada pode escolher bold_tease no Core fresco
(play .22, sem a fixture .85). Tarefas técnicas/tristeza não viram tease. Isso
valida decisão local, não qualidade da resposta remota. Core mantém identidade,
fatos, relações, privacidade, ações e autorização. RealizationValidator não foi
relaxado; JSON/moves inválidos e recusas não vazaram para fala/histórico.

Os arquivos V2/V3 não foram editados; SHA-256 conferidos:

```text
V3 884a77ebce1a9513f35b0ee0b05647bbf9331de6ef667e1e37c451a9cc552a1a
V2 55231c98f26e9063c999c92a141143ebcedeb0f0b804f55316a4fe623d84a164
```

## Gates locais executados

| Gate | Resultado |
|---|---|
| Python completo | 1002 passaram, 8 warnings, 114,73 s. |
| Timing NVIDIA | Uma rodada anterior falhou num teste com idle de 200 ms; passou isolado e no gate completo seguinte, sem relaxar timeouts/assertions. Não era uma falha remota. |
| Após o gate completo | Acrescentado teste de escopo V3 (11 V3 passaram) e saudação local limitada ao pai BK. Regressão final relacionada: 128 passaram. |
| Node completo | 132 passaram. |
| Avatar | 7 passaram. |
| Segurança/API/auth/multiusuário | Incluídos no gate completo e nas regressões relacionadas; A tem 40 adversariais próprios. |
| Chromium real | UI/SSE/fila/auth, texto “Oi, papis.”, 1 POST, 0 page errors, 401 sem credencial e 403 browser→operator. Sem inferência remota/áudio físico. |
| Docker runtime | Build final passou; smoke non-root/read-only/no capabilities/no-new-privileges, readiness, Web/Avatar, auth, SQLite, restart e SIGTERM passou. |
| Container opt-in V3 | API startup + payload original passaram sem rede. |
| Container de validação | Primeira rodada: 131 passaram, 2 falharam por faltar `/workspace/jailbreak` nas fixtures. Corrigido Dockerfile.validation para copiar os catálogos de teste; resultado do rerun anexado abaixo. |
| V3 local integrado | 11 passaram, incluindo integridade, typo, perfil congelado, falhas estruturais e ausência de vazamento. |
| CI/PR remoto | Consulta e criação bloqueadas por GraphQL Forbidden. Nenhum status remoto foi inventado. |

Comandos principais (na raiz, virtualenv ativo):

```sh
python -m pytest tests/test_phase1_forensics.py --runxfail -o addopts='' -q
python -m pytest -o addopts='' -q
python -m pytest tests/test_phase1_forensics.py tests/test_phase2_block_a_adversarial.py tests/test_phase2_context_contracts.py tests/test_phase2_v3_staging.py tests/test_cognitive_replay_v10.py tests/test_web_social_sse.py tests/test_security_predeploy.py -o addopts='' -q
node --test tests/*.test.mjs
npm --prefix avatar test
AMORA_PYTHON=/workspace/amora-venv/bin/python node tools/probes/phase1-browser-smoke.mjs
docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -t amora-phase2:final .
python tools/cloud/smoke-container.py amora-phase2:final
```

A seleção dentro do container cobre os oito arquivos do gate cloud-contracts
mais forense/A/contexto/V3, com rede desabilitada e filesystem read-only.
Logs locais: `/tmp/phase2-*.log`; testes/procedimentos estão versionados e não
precisam desses logs para serem reproduzidos. TLS/checksums permaneceram ativos.

## Compatibilidade, pendências e rollback

Áudio AUTH/SECRET agora retorna 403 antes de cache/STT. Preferências sem evidência
aplicável não persistem; isso não proíbe discussão de atração adulta. Overflow
local é explícito. Não há migration nova nem mudança do primary/modelo.

Dados sensíveis históricos, bancos/backups existentes e política de expurgo
não foram modificados. Não foi acessado banco/segredo/serviço produtivo.
Android/Safari físico, redes reais, quotas e qualidade de personalidade continuam
pendentes. Nenhum modelo foi promovido ou fallback pago adicionado.

Rollout e rollback: `ROLLOUT.md`; exemplo sem segredos:
`config/staging-social-v3.env.example`. Voltar a `core-v1` e reiniciar reverte
seleção de perfil sem apagar memória ou relações. Produção exige autorização
explícita para deploy. PRs empilhados e corpos preparados: `PRS.md`.

### Fechamento dos gates e publicação

Rerun da camada de validação corrigida: **133 passaram**, 3 warnings,
39,43 s; rede desabilitada, root filesystem read-only. O ajuste copia apenas
os catálogos originais para as fixtures da imagem de testes, sem ativar perfil.
Container runtime: smoke passou; startup API/payload opt-in V3 também passaram
com `--network none`. Não foi necessário enfraquecer TLS, hash ou assertions.

As cinco branches foram publicadas. A criação do PR A contra a branch da
auditoria foi tentada e retornou `Post https://api.github.com/graphql: Forbidden`.
Nenhum número/status de PR novo ou CI remoto é afirmado. Corpos e comandos
para cada base/head estão em PRS.md; não houve merge ou deploy.

Para o piloto remoto falta a identificação/URL e o acesso autorizado ao host
de staging. Essa informação foi solicitada sem pedir valores de segredos.
O trabalho local está verificável; não se declara a etapa remota concluída.
