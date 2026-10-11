# PROJECT_STATE — Amora-Chibi

**Última atualização:** 2026-10-11  
**Objetivo:** permitir continuidade segura entre chats/agentes sem depender do histórico integral da conversa.

Este arquivo é um **snapshot operacional**. Para regras atuais, sempre leia primeiro `docs/canon/*`.

---

## 1. Fonte de verdade

Ordem de leitura:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/canon/DIALOGUE_EMOTION.md`;
5. `docs/canon/PROVIDER_MESH.md`;
6. `docs/canon/VOICE_IDENTITY.md`;
7. `docs/roadmap/VERSIONS.md`;
8. `docs/decisions/`;
9. este `PROJECT_STATE.md`;
10. docs técnicos específicos de versão/módulo.

Docs antigos não são autoridade atual quando divergirem do canon.

---

## 2. Invariante de identidade

```text
Provider = capacidade cognitiva/linguística substituível
Amora Core = identidade + estado + memória + relações + emoção + drives + intenção + causalidade
```

A Amora não é um LLM.

Trocar Groq/Gemini/Cloudflare/OpenRouter/outro provider não pode trocar `ENTITY_AMORA`.

---

## 3. Plataforma

Host principal atual:

```text
Galaxy A15 5G
├── Termux / Python → Amora Core
└── Android / Kotlin → microfone, áudio, UI, foreground service, pairing e voz local
```

Backend: modular monolith, SQLite, asyncio/FastAPI, Event Bus e APIs locais.

---

## 4. Estado técnico V1 integrado

### Persistência cognitiva

Schema V1 inclui `conversation_messages`, `cognitive_journal`, `speech_segments` e `model_usage`.

Restart e backup/restore preservam estado cognitivo persistente.

### Context Compiler

Compila somente contexto relevante a partir de Self Model, relationships, mood/affect, drives, intenção ativa, memories, World Model, conversa literal recente, privacy/remote authorization e DialoguePlan.

O turno humano atual aparece uma única vez para o provider. Turnos técnicos marcados com `test_mode` são excluídos como unidade causal do histórico futuro.

### Dialogue Identity — Step 2 concluído

O Core possui `DialoguePlan` local e autoritativo com:

- dialogue act;
- objective/moves;
- stance;
- relational attitude;
- closing mode;
- follow-up/help policy;
- facts/grounding;
- tone tags derivados de estado local.

Atos incluem acknowledgement, self-fact, disclosure, support, resposta, continuação, opinião, discordância, reação brincalhona, reação relacional, boundary, autonomia e reflection.

O provider não joga prose livre diretamente na fala. Fluxo atual:

```text
DialoguePlan
→ provider gera RealizationFrame estruturado
→ validator local
→ somente então texto falável / SpeechSegment
```

Move não autorizado, follow-up/help proibido ou closing incompatível é rejeitado antes de qualquer fala.

Self-facts simples e vocativos podem ser realizados localmente sem provider.

### Emotion Engine — loop fechado

O Core possui appraisal determinístico, Emotion Events, Global Mood, Entity Affect, decay, blame, intentionality, spillover e Social Bids.

A conversa normal agora alimenta o Emotion Engine **antes** da compilação cognitiva do mesmo turno por meio de `InteractionAppraiser` local e conservador.

Consequências:

- uma interação social significativa pode alterar mood/affect;
- o mesmo turno já pode receber stance/attitude compatíveis com o estado atualizado;
- perguntas neutras não fabricam emoção;
- emoção influencia comportamento social antes da prosódia;
- `test_mode` pode calcular preview sem persistir mood/affect.

Observabilidade inclui último appraisal, último Emotion Event/profile e último turno de teste.

### Test-state isolation

Turnos técnicos podem usar:

```text
test_mode=true
memory_consolidation=false
relationship_learning=false
emotion_persistence=false
```

Reflexão persistente é bloqueada nesses turnos; entrada + saída técnica são excluídas do histórico literal futuro e emoção pode ser apenas efêmera.

Existe rota autenticada `/v1/conversation/test-turn` para probes isolados.

### Addressee continuity

Uma chamada explícita abre janela curta de continuidade. Follow-ups inferidos podem usar o tempo restante, mas **não renovam** a janela indefinidamente.

### Provider resilience / mesh — Step 3 concluído

Malha V1:

1. Groq primary enquanto saudável;
2. Cloudflare Workers AI como segundo eixo direto, quando explicitamente confirmado zero-cost;
3. Gemini como fallback independente quando configurado;
4. OpenRouter em rota explicitamente gratuita como broker/specialist/último recurso;
5. fallback local.

Cloudflare e OpenRouter usam adapters estruturados compatíveis com `RealizationFrame` e reflection proposals.

Guardas de custo:

- Cloudflare só registra se token + account id existirem **e** `AMORA_CLOUDFLARE_ZERO_COST_CONFIRMED=true`;
- adapter Cloudflare usa Workers AI direto, não Unified Billing;
- OpenRouter só fica disponível para `openrouter/free` ou modelo `:free`;
- modelo OpenRouter não-gratuito falha fechado;
- ausência de credencial apenas remove aquele provider da malha;
- quota/preço atual não é hardcoded como contrato eterno.

Router continua selecionando por capability, privacy, saúde, quota, custo relativo e prioridade. Cooldown/backoff/failover continuam locais.

Health report expõe providers configurados/registrados e nomes de providers sem expor secrets.

### Privacidade

Android possui autorização explícita para cognição remota de falas do usuário, separada de STT/áudio.

`AMORA_REMOTE_COGNITION_ALLOWED` é fallback/controle para cognição remota autônoma quando o evento não traz autorização explícita; não substitui o toggle normal do app.

AUTH/SECRET não vão para providers remotos.

---

## 5. Fechamento V1 — quatro passos

### Step 1 — Local Voice

**Status:** `PASS / MERGED` — PR #27.

Entregue:

- `VoiceIdentity` pt-BR provisória;
- sherpa-onnx `OfflineTts`;
- Piper/VITS Faber provisório;
- voice pack empacotado em build-time assets e SHA verificado;
- PCM local via AudioTrack;
- Gemini TTS fora do caminho normal;
- sem fallback automático silencioso para Google/Android TTS;
- diagnóstico de voz no app;
- confirmação física do Faber no Galaxy A15.

Faber é técnico/provisório e masculino.

### Step 2 — Dialogue Identity + Emotion Integration

**Status:** `PASS / MERGED` — PR #29, merge `dfe50de9a9312f0e6a3f08cc482e9b4dc0aee1d4`.

Entregue:

- DialoguePlan rico;
- RealizationFrame estruturado + validator local;
- local realization para atos triviais;
- emotion loop conversa → appraisal → Emotion Engine → diálogo;
- test-state isolation;
- observabilidade de appraisal/realization;
- addressee continuity sem auto-renovação;
- correção da ordem causal de `AUTONOMOUS_INITIATION`/`COGNITIVE_REFLECTION` antes do plano.

CI final: Python 3.11–3.14 PASS, **211 pytest tests** por matriz + doctor/autonomy/actions/conversation/avatar/backup smokes PASS.

### Step 3 — Zero-Cost Provider Mesh

**Status:** `PASS / READY TO MERGE` — PR #30.

Entregue:

- `docs/canon/PROVIDER_MESH.md`;
- Cloudflare Workers AI direct adapter;
- OpenRouter free-route adapter;
- integração com structured realization/reflection;
- zero-cost guards fail-closed;
- health/usage observability;
- deterministic offline tests sem consumir API real.

CI run `38172639477`: Python 3.11, 3.12, 3.13 e 3.14 PASS; pytest e todos os smoke tests PASS.

### Step 4 — Canonical Voice + V1 Certification

**Status:** `NEXT / VOICE TARGET CANONIZED`.

Próximo escopo:

- seguir `docs/canon/VOICE_IDENTITY.md`;
- selecionar material feminino pt-BR com direitos/provenance claros;
- priorizar GPU gratuita para treino/fine-tuning;
- escolher medium/high por qualidade + benchmark, não pelo rótulo;
- exportar ONNX e empacotar no mesmo runtime sherpa-onnx já comprovado;
- calibrar Prosody Planner bounded;
- teste prolongado no A15;
- conversa/barge-in/provider failover/restart/backup-restore;
- estabilidade térmica e de memória;
- fechamento documental V1.

Treino pago não é requisito. Se compute pago algum dia for avaliado, exige teto explícito de custo e decisão consciente. RAM do A15 não equivale a VRAM de GPU; o A15 permanece alvo de inferência/certificação por padrão.

---

## 6. Regra de testes

Um teste só deve existir se o resultado:

1. decidir entre arquiteturas reais;
2. provar um gate físico/hardware;
3. detectar/prevenir regressão crítica;
4. validar contrato que entra em produção.

Evitar builds/experimentos descartáveis apenas por curiosidade quando não alteram decisão de projeto.

---

## 7. Roadmap posterior

### V1.1

- percepção visual;
- screen mapping;
- corpo/avatar final;
- renderer/rig/expressões necessários;
- integração semântica Mind → Avatar.

### V1.2

- desejo estético persistente;
- escolhas de aparência;
- avaliação de roupa;
- preferências visuais;
- propriedade sobre autoimagem.

Planejamento antigo V0.9/V0.95 foi dividido. Esses itens foram movidos, não cancelados.

---

## 8. Git / Android workflow

BK usa GitSync para atualizar o diretório compartilhado e não quer credenciais GitHub no Termux.

```bash
bash /storage/emulated/0/Documents/Amora-Chibi/tools/termux-sync.sh \
  /storage/emulated/0/Documents/Amora-Chibi

source ~/.venvs/amora/bin/activate
cd ~/amora-src
amora serve --port 8765
```

Não pedir GitHub token no Termux.

Provider secrets ficam no SecretStore local da Amora, nunca em chat/repo.

---

## 9. Regras de continuidade

Antes de sugerir arquitetura nova:

- ler canon/roadmap/ADRs;
- verificar PRs ativos;
- não reintroduzir Gemini TTS como voz normal;
- não transformar provider em identidade;
- não inventar self-facts;
- não tratar blacklist como controle social principal;
- não mapear emoção diretamente para pitch/volume como comportamento;
- não deixar testes técnicos consolidarem estado persistente por padrão;
- não ativar billing/pago silenciosamente;
- não declarar hardware PASS com base apenas em CI;
- não expandir V1 com V1.1/V1.2 sem decisão explícita.

Se a mesma área entrar em patch loop, parar e reavaliar ownership antes de continuar.
