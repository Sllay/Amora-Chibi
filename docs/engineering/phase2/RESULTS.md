# Fase 2 — execução incremental

Base: audit/phase1-core-forensics 452a4d9 (ref PR166 confirmado via Git).
Main remoto7504d23 não contém auditoria; branches empilhadas preservam testes.
Estado/CI de PR166 não consultável: API GitHub GraphQL Forbidden. Nenhum merge/deploy.
Relatório Fase1 preservado como evidência histórica.

## A — privacidade e integridade

Antes: reprodução original --runxfail confirma11falhas/6pass (log local
/tmp/phase2-before.log). Baseline adversarial agente17fail/8pass.
Correções: AUTH/SECRET redigidos antes de inferência, floor, evento e retorno;
extra_payload descartado para escopos protegidos; messaging bloqueado antes
attention/audit; áudio protegido bloqueado antes de cache/STT; metadata de
histórico protegido descartada. Nenhum segredo real usado.
Atribuição separada em language/attribution.py: spans/atores/destinos e cláusulas,
sem lista de frases excepcionais. Report e disclosure não concedem escolha.
Commit recompila elegibilidade e confere evidência vinculada a texto/turno/ator/
destino/versão e consentimento/escopo; modo teste continua sem escrita.
As cinco reproduções A deixam de ser xfail somente após passarem.
Suites A:132pass/6xfail antes do último endurecimento de atribuição; rodada
específica posterior63pass, sem falhas. Xfails restantes pertencem aos blocos B–D.
Risco residual: atribuidor é gramática local conservadora, não parser geral;
ambiguidade nega preferência persistente sem proibir expressão adulta.

## B — transporte

F06 reprodução inicial falha; após catch tipado NvidiaStreamTimeout antes do
TimeoutError do caller:48pass/5xfail em forense+provider+SSE/budget.
Código NVIDIA_IDLE_TIMEOUT ou NVIDIA_TOTAL_TIMEOUT, etapa sanitizada e backoff
ficam no journal; caller curto mantém SURFACE_BUDGET_TIMEOUT sem health poison.
Fila Web agora inclui generation(fallback/provider/reasons); UI mostra fallback
como recuperação, sem fingir rejeição estrutural. Node Web executado.

## C — contexto

Antes: F04/F05/F10 falham como auditoria. Após:40pass/2xfail (somente D).
Budget é estimativa de envelope canonical e guarda payload NVIDIA antesHTTP;
excesso irredutível gera CONTEXT_ENVELOPE_OVERFLOW, não health failure.
Prioridade: retirar capabilities opcionais e claims/memórias de menor ranking,
depois histórico antigo, conservando última troca causal. Replanejar snapshot
final. Default8192 substitui3200 que antes ignorava prefixo de protocolo.
Reproduções F04/F05 agora aceitam explicitamente overflow em orçamento256;
não se força perda de dado essencial para satisfazer budget impossível.
ACIR projetado remove content literal de system; ConversationTurn conserva bytes.

## D — contratos

Parentesco por arestas direcionais em identity/kinship.py; father_of aponta
do pai à filha e child_of no inverso. Replay aceita realize(), usa transport
sem wrapper de repair/state, valida output local e não comita escolhas/usage.
Não reutiliza respond() para executar replay. F08/F09 sem xfail.

## Revisões independentes e perfil V3

Revisores existentes amora test/core especionar reutilizados; nenhum terceiro.
Adversariais adicionais do A40pass; contexto7pass; staging local V3 inicialmente
10pass. A revisão encontrou e fechou: lista ACIR aninhada, plano crescendo após
última estimativa, reflection overflow escapando, códigos/planner desatualizados,
e contexto compartilhado entre replay streams. Classificação do replay também
preserva RealizationError/overflow em vez de atribuir tudo ao provider.

Perfil explícito AMORA_SOCIAL_EXPRESSION_PROFILE=amora-v3; checksum original
verificado antes da API e da compilação/payload. Snapshot congela perfil;
last_realization registra uso por turno; state indica configuração, não leitura
remota/qualidade. Default/rollback core-v1. V3 integra realização real social
NVIDIA e protocolo compartilhado dos fallbacks; removida instrução legada de
saída somente falável nas realizações nativas Groq/OpenRouter/Gemini que
competia com JSON. Validador não teve regra lexical/semântica relaxada.

Staging efetivo: somente instância local isolada TestClient, API/SSE autenticados,
compiler/estado reais e transporte NVIDIA simulado. Nenhum host remoto foi
configurado, nenhum deploy ou ativação de produção. São pendências operacionais,
não uma certificação de naturalidade Fase3.

Riscos/limites: atribuição é gramática conservadora, não compreensão arbitrária;
estimativas de token não são tokenizer exato. Groq pode chamar especialista
antes de overflow do surface: código correto NO_SURFACE_REQUEST. Histórico
sensível anterior, backups e migração de retenção não foram apagados. Não houve
acesso ao Neon, segredo real ou mudanças na configuração de produção.

## Portões executados

- Todas as11 assertions forenses passaram semxfail (arquivo17casos total).
- Suíte Python completa:1002pass/8warnings,114.73s. Uma rodada anterior
  falhou em1teste de timingidle200ms; rerun isolado1pass e rodada completa
  seguinte1002pass, sem relaxar timeouts/assertions. Não foi falha remota.
- Depois do gate completo: acrescentado teste de escopoV3 (11V3pass) e
  saudação local restringida ao paiBK para não chamar mãe de papis.
  Regressão relacionada final128pass (API,segurança,auth,forense,replay,A/C/V3).
- Node completo132pass; Avatar7pass; Chromium real UI/SSE/fila/auth passou,
  respostaOi,papis,1POST,0pageerrors,401semcredencial/403web->operator.
- V3staginglocal: APIreal/estado/compilador/fila/validator, sóHTTPprovider
  simulado. Os bytes originais chegaram ao payload; JSON/moveinválido/recusa
  não vazaram na entrega. Isso não mede naturalidade real nem aderênciaLLM.
- SHA-256V2/V3 preservados; nenhum segredo real no teste/relatório.
- Build/smoke Docker preliminares passaram. Build/runtime final em execução;
  resultado será anexado. TLS/checksums não foram desativados.

### Rastreabilidade de correções

| Defeito | Componente responsável | Prova posterior |
|---|---|---|
| F07 | language.pipeline, fronteiras audio/messaging, CognitiveStore metadata | forense +40adversariais A incluindoAUTH eSECRET separados |
| F01/F03 | language.attribution +DialogueOrchestrator | direto/citado/indireto, sufixo após aspas e disclosure |
| F02/F02b | elegibilidade atribuída +commit_self_choices revalida evidência | legítima uma vez; stale/forged/terceiros semescrita |
| F06 | structured_runtime catchNVIDIA antesTimeoutError | erroprovider/budgetcaller distintos,journal e metadatageraçaoWeb |
| F04/F05 | context_budget +fit/replan/prioridades/guardsnativos | overflow explícito,últimatroca/autoria preservados,7testesC |
| F10 | projeção ACIR recursiva | literalforasystem,dentroConversationTurn;listsaninhadas |
| F08 | identity.kinship direcional | father_of correto,sem substringterceiros |
| F09 | production.replay structured +deepcopy amboscaminhos | respond/replayMistral,validator,semcommitescolhas;legado preservado |

Mudanças de compatibilidade: escopos protegidos no endpointáudio agora recebem
403antescache/STT. Preferências semevidência/contextorequestconsistente deixam
deser persistidas; conversação/opinião nãoéproibida. Budgetimpossível gera
resultado local explícito. Defaultbudget8192 éestimado,nãogarantia de tokenização.
Staging optinV3 altera linguagem/atos sociais adequados, não modelo/primary,
permissões/ações/identidade. Não há migration nova nem limpeza de banco.
