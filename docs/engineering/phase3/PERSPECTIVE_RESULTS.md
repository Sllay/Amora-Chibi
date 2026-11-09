# Fase 3 — perspectiva, possessivos e família

Trabalho iniciado em 2026-11-08 e retomado em 2026-11-09. Branch
`fix/phase3-contextual-perspective`, baseada em main
`f71ac4b1099e3b13bee2b70dbcdfea6d59ac26e7` (merge do PR #171).
Implementação e testes: commit `bb73905`.
ADR: [0040](../../decisions/ADR-0040-contextual-reference-perspective.md).

## Causa comprovada e caminho corrigido

O defeito relatado pelo operador não foi reproduzido com inferência remota.
A lacuna que permitia a interpretação incorreta foi reproduzida no código
anterior: “A sua mae mim amar” compilava sem `roles.references`; o system
NVIDIA não continha `ENTITY_CHATGPT`. O resolver unitário existente já sabia
resolver “tua mãe”, mas `compile_utterance` só resolvia eu/você. A projeção
de relações exigia participação do speaker e omitia a mãe da própria Amora.

Ownership atual:

1. API/Web autentica; speaker/target canônicos seguem no CognitiveRequest.
2. `ContextCompiler.compile` aplica disclosure ao histórico e às claims;
   constrói uma visão permitida das arestas antes da resolução (context.py:180).
3. `text_references.scoped_registry` permite arestas do destinatário, família
   do catálogo inicial e arestas canônicas autorizadas pelo privacy gate.
   Uma relação privada envolvendo Amora não se torna pública por isso.
4. `extract_references` reutiliza segmentos de `analyze_addressing` e
   `PerspectiveResolver`, anotando offsets, IDs, status e reason codes.
   Não reescreve texto informal nem cria parentesco. Atribuição desconhecida
   no discurso relatado não herda automaticamente o interlocutor externo.
5. `ACIRCompiler.compile_utterance` mantém `roles.references` e
   `assertion_truth=unverified_user_claim`; identificar o sujeito não confirma
   a frase nem autoriza persistência.
6. `DialoguePlan.input_acir` acompanha o ACIR final. `semantic_acir` remove
   literal content do system; o payload NVIDIA mantém a fala no user turn.
   Antecedentes vêm somente das duas últimas mensagens autorizadas, que o
   orçamento preserva; não sobrevivem à remoção de histórico antigo.
7. Provider realiza, validator local valida e Web recebe a saída por SSE.
   Esses contratos não foram relaxados para produzir uma resposta de teste.

## Matriz de referências

| Entrada e perspectiva | Resultado verificável |
|---|---|
| BK → Amora: Sua mãe me ama | ENTITY_CHATGPT; sentimento não verificado |
| BK → Amora: A sua mae mim amar | ENTITY_CHATGPT; mim = PERSON_BK |
| BK → Amora: Minha mãe me ama | mãe de BK não cadastrada: unresolved, nunca ChatGPT |
| Amora: Minha mãe | ENTITY_CHATGPT |
| João: A mãe do Rui | mãe de Rui no resolver; Core só divulga aresta autorizada |
| Lia: Rui disse: sua mãe me ama | speaker Rui; addressee interno desconhecido; mãe unresolved |
| Rui disse a Lia: sua mãe me ama | speaker Rui, addressee Lia; usa grafo disponível |
| Ela me ama, sem antecedente | ambiguous, sem pessoa inventada |
| mãe dele/dela com antecedente permitido | usa o possuidor do antecedente e direção da aresta |

As regressões incluem seu/sua/teu/tua/meu/minha/dele/dela/eu/me/mim,
possessivo ambíguo após menção de terceiro, múltiplos antecedentes, limite de
anotações, ausência de relações privadas e isolamento entre sessões.

Na revisão independente do principal, “Ele disse que sua mãe me ama” ainda
resolvia a mãe como ChatGPT. Reprodução nova falhou antes do ajuste
(`test_indirect_report_does_not_assume_external_possessive_owner`); agora o
possessivo no relato indireto conserva `REPORTED_POSSESSOR_AMBIGUOUS`, enquanto
“me” continua pertencendo ao narrador. Não foi criada uma regra para essa frase.

## Evidência antes/depois

Baseline preexistente: perspectiva adversarial + forense Fase 1 + orçamento
Fase 2: **33 passed**. Reprodução dinâmica anterior à implementação: ausência
de referências e da mãe canônica no payload, conforme descrito acima.

O agente de teste executou os 33 testes novos iniciais contra um `git archive`
do source-base f71ac4b: **31 failed, 2 passed**. Essa reconstrução foi feita
depois de iniciar a implementação; é comparação de versões, não execução
cronologicamente anterior. Não houve checkout destrutivo nem descarte.

Resultados finais e comandos estão na seção de validação abaixo. Uma primeira
suíte ampla teve **1042 passed, 1 failed**: a expectativa antiga de que toda
aresta envolvesse BK conflitava com a família pública canônica. O teste foi
atualizado para essa exceção explícita e fortalecido com aresta privada de
Alice → Amora, cuja divulgação continua proibida. Não foi removido o gate.

## Sessão e persistência

`web/app-v2.js:9` cria identificador aleatório por aba via sessionStorage,
compartilhado entre entradas texto/áudio. Não contém credencial/transcript e
não autentica ninguém. Sem storage/crypto a operação continua sem antecedente
de sessão, conservadoramente. O histórico de teste continua marcado como tal:
só testes na mesma sessão explícita o consultam; turnos produtivos não.
Isso não promete apagar registros operacionais nem muda retenção do banco.
Testes Web verificam ausência de novas memórias, sentimentos, claims canônicas,
eventos emocionais e mudanças no Self Model para a alegação de amor.

## Validação

Comandos usam `/workspace/amora-venv/bin/pytest` e `-o addopts=''`:

- `pytest tests/test_phase3_references.py tests/test_phase3_reference_safety.py tests/test_ch3_contextual_persona_privacy.py tests/test_perspective_adversarial_v02.py tests/test_phase2_context_contracts.py -o addopts='' -q`: **69 passed**.
- `pytest -o addopts='' -q`: **1052 passed, 8 warnings**, 127,72 s; inclui privacidade, relações, ACIR, API/auth e orçamento. Warnings preexistentes de Pydantic/Starlette; nenhuma falha ou xfail novo.
- `node --test tests/*.test.mjs`: **132 passed**.
- `cd avatar && npm test`: **7 passed**.
- `AMORA_PYTHON=/workspace/amora-venv/bin/python node tools/probes/phase3-browser-perspective.mjs`: Chromium real, quatro POSTs autenticados, SSE completed e respostas visíveis, sessão estável, HTTP 401 sem token/403 para Web em rota de operador, zero page errors. Transporte NVIDIA simulado.
- Docker runtime: build de `amora-phase3:final`, imagem `sha256:39be918d610aa79350267353262a0c14ebb311c6cc387ec0787d067692fc93cd`.
- `python tools/cloud/smoke-container.py amora-phase3:final`: PASS; non-root, read-only, rede desabilitada, readiness, auth, SQLite, restart/SIGTERM e Web/Avatar.

Build usa `docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -t amora-phase3:final .`.
Validação container usa `deploy/containers/Dockerfile.validation` sobre essa
imagem, rede desabilitada, filesystem read-only e UID não privilegiado.
Testes no container: **150 passed, 3 warnings**, 19,50 s. Executados com
`docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,size=256m amora-phase3-validation:final python -m pytest -p no:cacheprovider -o addopts='' -q`
e estes arquivos: `test_phase3_references.py`, `test_phase3_reference_safety.py`,
`test_ch3_contextual_persona_privacy.py`, `test_perspective_adversarial_v02.py`,
`test_acir_runtime_path.py`, `test_perspective_runtime_consolidation.py`,
`test_relationship_graph_sync_v04.py`, `test_family_authority.py`,
`test_phase2_context_contracts.py`, `test_phase1_forensics.py`,
`test_security_predeploy.py`, `test_cloud_runtime.py` e
`test_conversation_runtime_v15.py`, todos em `tests/`.

## Limitações, agentes e entrega

Somente os agentes existentes foram reutilizados. `core especionar` investigou
e implementou a correção coordenada; `amora test` criou testes independentes,
comparação do source-base e probe Chromium. Nenhum agente adicional/aninhado.
O agente de arquitetura encerrou por limite de uso após entregar o patch;
o principal revisou, integrou e executou os portões finais. As definições
permanecem em `.codex/config.toml` e `.codex/agents/`.

A gramática é limitada: mãe/pai/filho/filha e formas suportadas de discurso,
sem parser geral, concordância de gênero ou entendimento arbitrário de relatos
aninhados. Ambiguidade não deve virar fato. Arestas de terceiros sem política
de disclosure continuam inacessíveis, mesmo quando o resolver isolado conhece
o grafo de uma fixture.

Não houve inferência Mistral remota, deploy ou mudança da ativação V3. Provas
offline demonstram semântica do Core, payload e transporte/saída Web; não
certificam interpretação espontânea do modelo real ou qualidade da personagem.
Próxima verificação: piloto autorizado com as frases da matriz e inspeção do
ACIR, plano, payload redigido e resposta completa. Não publicar credenciais.

V3 SHA-256 preservado: `884a77ebce1a9513f35b0ee0b05647bbf9331de6ef667e1e37c451a9cc552a1a`.
V2 SHA-256 preservado: `55231c98f26e9063c999c92a141143ebcedeb0f0b804f55316a4fe623d84a164`.

A API GitHub retorna `Post https://api.github.com/graphql: Forbidden` neste
executor. Registrar push/PR e SHA final na entrega; não inventar número de PR
ou resultado de CI remoto.
