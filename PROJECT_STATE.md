# PROJECT_STATE — Amora-Chibi

**Última atualização:** 2026-10-16  
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
8. ADRs relevantes em `docs/decisions/`;
9. este `PROJECT_STATE.md`;
10. docs técnicos específicos de versão/módulo;
11. código, main, PRs e workflows atuais.

Docs antigos não são autoridade atual quando divergirem do canon/ADR vigente.

---

## 2. Invariante de identidade

```text
Provider = capacidade cognitiva/linguística substituível
Amora Core = identidade + estado + memória + relações + emoção + drives + intenção + causalidade
```

A Amora não é um LLM.

Trocar Groq/Gemini/Cloudflare/OpenRouter/outro provider não pode trocar `ENTITY_AMORA`.

A mesma regra agora vale para voz:

```text
TtsBackend != identidade vocal
```

A identidade vocal é uma composição versionada de speaker/base + VoiceSignature + prosódia bounded.

---

## 3. Plataforma atual

Host principal atual:

```text
Galaxy A15 5G
├── Termux / Python → Amora Core
└── Android / Kotlin → microfone, áudio, UI, foreground service, pairing e voz local
```

Backend: modular monolith, SQLite, asyncio/FastAPI, Event Bus e APIs locais.

Comandos operacionais atuais no Termux:

```bash
source ~/.venvs/amora/bin/activate
cd ~/amora-src
amora serve --port 8765
```

Pareamento:

```bash
source ~/.venvs/amora/bin/activate
cd ~/amora-src
amora pair
```

BK usa GitSync para atualizar o diretório compartilhado e não quer credenciais GitHub no Termux.

---

## 4. Estado técnico V1 integrado

### Persistência cognitiva

Schema V1 inclui `conversation_messages`, `cognitive_journal`, `speech_segments` e `model_usage`.

Restart e backup/restore preservam estado cognitivo persistente.

### Context Compiler

Compila somente contexto relevante a partir de Self Model, relationships, mood/affect, drives, intenção ativa, memories, World Model, conversa literal recente, privacy/remote authorization e DialoguePlan.

O turno humano atual aparece uma única vez para o provider. Turnos técnicos marcados com `test_mode` são excluídos como unidade causal do histórico futuro.

### Dialogue Identity — Step 2 concluído

O Core possui `DialoguePlan` local e autoritativo com dialogue act, objective/moves, stance, relational attitude, closing mode, follow-up/help policy, facts/grounding e tone tags derivados de estado local.

Fluxo atual:

```text
DialoguePlan
→ provider gera RealizationFrame estruturado
→ validator local
→ somente então texto falável / SpeechSegment
```

Self-facts simples e vocativos podem ser realizados localmente sem provider.

### Emotion Engine — loop fechado

O Core possui appraisal determinístico, Emotion Events, Global Mood, Entity Affect, decay, blame, intentionality, spillover e Social Bids.

A conversa normal alimenta o Emotion Engine antes da compilação cognitiva do mesmo turno por meio de `InteractionAppraiser` local e conservador.

### Test-state isolation

Turnos técnicos podem usar:

```text
test_mode=true
memory_consolidation=false
relationship_learning=false
emotion_persistence=false
```

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

Guardas de custo permanecem fail-closed.

### Privacidade

Android possui autorização explícita para cognição remota de falas do usuário, separada de STT/áudio.

AUTH/SECRET não vão para providers remotos.

---

## 5. Fechamento V1 — estado real

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

### Step 3 — Zero-Cost Provider Mesh

**Status:** `PASS / MERGED`.

PR #30 já foi integrado ao `main`. O provider mesh faz parte da fundação atual.

### Step 4 — Canonical Voice + V1 Certification

**Status:** `IN PROGRESS — VOICE LAB MERGED / PHYSICAL TEST PENDING`.

A estratégia de treino próprio foi **superseded** pelo ADR-0009.

Nova direção:

```text
TtsBackend local pronto
↓
speaker/base escolhida no hardware
↓
VoiceSignature determinística/versionada
↓
AMORA_CANONICAL
↓
Prosody Planner bounded
↓
PCM / AudioTrack
```

Treinar/fine-tunar modelo próprio **não é requisito da V1**.

---

## 6. Voice Lab — estado atual

O `main` contém o Voice Lab isolado, merge:

```text
5cf93d5975f659adce9748f155dbd962a7140750
feat(v1.0): add isolated Supertonic Voice Lab
```

Implementação:

- `android/app/src/main/java/dev/amora/chibi/voice/lab/SupertonicVoiceLabEngine.kt`;
- `android/app/src/main/java/dev/amora/chibi/voice/lab/VoiceLabActivity.kt`;
- launcher separado no `AndroidManifest.xml`;
- `prepare-deps.sh` empacota o modelo Supertonic do laboratório;
- produção `LocalVoiceEngine` permanece Faber e não foi trocada.

Modelo do laboratório:

```text
sherpa-onnx-supertonic-3-tts-int8-2026-05-11
```

Runtime:

- sherpa-onnx `1.13.8`;
- CPU;
- 2 threads;
- idioma passado ao motor como `pt`;
- speakers vêm de `voice.bin` e são enumerados no aparelho.

Presets atuais:

- `NATURAL` — speed 1.00, silence 1.00, 8 steps;
- `AMORA_SOFT` — speed 0.95, silence 1.05, 8 steps;
- `AMORA_SYNTH` — speed 0.92, silence 0.90, 5 steps;
- `CHIBI` — speed 1.08, silence 0.90, 6 steps.

**Importante:** esses presets ainda NÃO são DSP de VoiceSignature. Eles não fazem pitch/formant; servem para benchmark inicial de base/speaker.

Frases fixas do Voice Lab:

1. `Bom dia. Como você dormiu?`
2. `Eu realmente não concordo com isso.`
3. `Você deixou suas coisas espalhadas pela sala.`
4. `Três pratos de trigo para três tigres tristes.`
5. `Essas escolhas precisam ser consideradas com cuidado.`
6. `Amor, você sabe que isso não faz o menor sentido, né?`

O laboratório mostra speaker/preset, duração e RTF.

### Gate físico pendente

BK precisa instalar a build atual no Galaxy A15 e testar:

- se o Supertonic abre;
- quantos speakers aparecem;
- quais speakers são femininos/adequados;
- pt-BR/inteligibilidade;
- pronúncia das seis frases;
- RTF/latência;
- travamentos;
- temperatura/RAM/CPU perceptíveis;
- melhor combinação speaker + preset.

A voz final só é escolhida **depois desse relato físico**.

---

## 7. VoiceSignature — próximo passo após escolher a base

Depois que BK escolher o melhor speaker/base:

1. congelar base vencedora;
2. implementar `VoiceProfile` separado de backend;
3. implementar `AMORA_CANONICAL`;
4. calibrar transforms bounded como pitch/formant/EQ/de-ess/dynamics/textura apenas quando necessários;
5. preservar `CHIBI`, `SYNTH` e futuros perfis como fantasias temporárias;
6. comparar A/B com a base limpa;
7. só então substituir Faber no caminho normal.

Perfil alvo continua:

- feminina;
- jovem adulta;
- pt-BR claro;
- doce sem infantilização;
- médio/médio-grave com brilho natural;
- conversacional;
- não locutora;
- não anime/chibi exagerada como voz normal.

---

## 8. Próxima frente após voz — Web/PWA para iPhone

Prioridade declarada por BK depois da voz:

- cliente Web/PWA instalável pela Tela de Início do Safari;
- uso diário no iPhone sem compilar APK a cada mudança;
- frontend separado do Core;
- Render Static Site ou hosting estático equivalente pode hospedar o cliente;
- modelos/pacotes web podem ser baixados na primeira execução e armazenados localmente quando possível;
- Android e Web devem compartilhar contratos, não duplicar a Amora.

Conceito:

```text
Amora Core / contratos comuns
        ├── Android platform adapter
        └── Web/PWA platform adapter
```

Não tratar PWA como "APK rodando no site".

### Hospedagem futura do Core

BK quer eliminar dependência de um celular sempre ligado.

Depois do cliente PWA, investigar Core remoto persistente com objetivo R$ 0, separando:

```text
Frontend/PWA
!=
Core persistente
!=
providers cognitivos/LLMs
```

Cloudflare Workers/Durable Objects/D1 ou equivalente são candidatos para pesquisa, mas **nenhuma migração do Core foi aprovada ainda**. Antes disso precisa ADR + prova de persistência, backup/restore, privacidade, custo e latência.

Não confundir `Cloudflare Workers` com `ChatGPT Work`.

---

## 9. Modularização — direção pedida por BK

BK quer reduzir scripts gigantes e impedir que novo recurso exija alterar o núcleo inteiro.

Taxonomia alvo:

```text
core/       -> identidade, estado, decisão e orquestração
contracts/  -> eventos, schemas, capabilities
modules/    -> voz, audição, visão, memória, presença
providers/  -> cognição, STT, TTS
plugins/    -> Discord/GitHub/etc.
platforms/  -> Android/Web
runtime/    -> registry/loader/event bus/lifecycle
```

Princípios:

- Core pequeno e estável, mas não "imutável";
- plugin falha sem derrubar Core;
- trocar TTS não toca identidade/cognição;
- criar cliente Web não duplica Core;
- adicionar Discord não exige cirurgia no núcleo;
- refactor deve acontecer incrementalmente em fronteiras reais, não como reescrita total especulativa.

---

## 10. Android build / teste

Workflow oficial prepara dependências e monta debug APK.

Localmente, quando necessário:

```bash
bash android/tools/prepare-deps.sh
gradle -p android --no-daemon :app:testDebugUnitTest :app:assembleDebug
```

APK:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

No APK atual o Voice Lab possui launcher próprio chamado **Amora Voice Lab**.

CI/build não substitui certificação física do Galaxy A15.

---

## 11. Regra de testes

Um teste só deve existir se o resultado:

1. decidir entre arquiteturas reais;
2. provar gate físico/hardware;
3. detectar/prevenir regressão crítica;
4. validar contrato que entra em produção.

Evitar builds/experimentos descartáveis apenas por curiosidade quando não alteram decisão de projeto.

---

## 12. Regras de continuidade

Antes de sugerir arquitetura nova:

- ler canon/roadmap/ADRs;
- verificar `main`, PRs ativos e CI;
- consultar ADR-0009 antes de sugerir novo ciclo de treino de voz;
- não reintroduzir Gemini TTS como voz normal;
- não transformar provider em identidade;
- não inventar self-facts;
- não tratar blacklist como controle social principal;
- não mapear emoção diretamente para pitch/volume como comportamento;
- não deixar testes técnicos consolidarem estado persistente por padrão;
- não ativar billing/pago silenciosamente;
- não declarar hardware PASS com base apenas em CI;
- não migrar o Core para cloud apenas porque um free tier existe hoje.

Se a mesma área entrar em patch loop, parar e reavaliar ownership antes de continuar.

---

## 13. Próximo trabalho imediato

Ordem atual:

```text
1. Galaxy A15: testar Voice Lab / Supertonic
2. escolher melhor speaker/base
3. implementar e calibrar VoiceSignature AMORA_CANONICAL
4. promover voz vencedora para produção
5. certificação final de voz/V1
6. iniciar cliente Web/PWA para iPhone
7. depois pesquisar/migrar hospedagem persistente do Core, se aprovada
8. continuar modularização incremental
```

**Não voltar para Colab/treino Kristin/Faber híbrido como próximo passo.** Esse caminho saiu do crítico da V1.
