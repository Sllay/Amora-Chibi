---
name: amora-engineering-skill
version: "1.0"
status: canonical
updated: "2026-10-10"
description: >
  Regras de continuidade, arquitetura, pesquisa, implementação, validação e
  documentação para qualquer pessoa ou agente trabalhando no repositório Amora.
---

# AMORA ENGINEERING SKILL — Regras Canônicas de Desenvolvimento

## 1. Missão

Ao trabalhar neste repositório, a prioridade é fazer a Amora crescer **sem perder coerência, identidade, histórico técnico ou capacidade de substituição de componentes**.

Não otimizar por “fazer funcionar uma vez”. Otimizar por:

- continuidade;
- modularidade;
- testabilidade;
- observabilidade;
- custo sustentável;
- privacidade;
- degradação graciosa;
- compatibilidade futura;
- capacidade de entender por que uma decisão existe.

---

## 2. Regra máxima de documentação

### 2.1 Fonte atual de verdade

A documentação atual da Amora vive no GitHub.

Ordem de leitura padrão:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/roadmap/VERSIONS.md`;
5. ADRs relevantes em `docs/decisions/`;
6. documentação específica da versão/módulo;
7. código, schemas e testes atuais;
8. histórico Git apenas quando necessário.

Uma instrução explícita e recente de BK pode mudar uma decisão, mas a mudança deve ser consolidada na documentação quando deixar de ser exploratória.

### 2.2 Documentos antigos não são especificação atual

Nunca usar automaticamente uma versão antiga de GDD, Architecture, Skill, roadmap ou documento de versão como autoridade atual.

Antes de reutilizar solução antiga:

1. verificar o documento canônico atual;
2. verificar `docs/roadmap/VERSIONS.md`;
3. procurar ADR relacionado;
4. verificar se o método foi `REJECTED`, `SUPERSEDED`, adiado ou dividido;
5. somente então considerar restaurá-lo.

> **Histórico existe para evitar repetição de erro, não para competir com o canon atual.**

### 2.3 Quando documentos entram em conflito

Não escolher silenciosamente.

Usar esta precedência:

1. decisão explícita mais recente consolidada;
2. ADR `ACCEPTED` mais recente que trate diretamente do assunto;
3. `docs/canon/ARCHITECTURE.md` para estrutura técnica;
4. `docs/canon/GDD.md` para produto/comportamento;
5. `docs/roadmap/VERSIONS.md` para escopo/versionamento;
6. código/testes atuais como evidência de implementação;
7. documento histórico somente como contexto.

Se código e canon discordarem, registrar e resolver a inconsistência; não fingir que não existe.

---

## 3. Versionamento documental

### 3.1 Canon vivo

`docs/canon/*` representa somente o estado vigente.

Ao mudar decisão:

- atualizar o canon;
- registrar o motivo em ADR quando a mudança for arquitetural/comportamental relevante;
- atualizar `VERSIONS.md` se escopo/versão mudou;
- deixar o Git preservar versões anteriores.

Não criar `GDD-final-final-v3.md` como sistema principal de histórico.

### 3.2 ADR — Architecture Decision Record

Criar ADR quando houver:

- mudança de arquitetura;
- abandono de método já implementado/testado;
- escolha entre alternativas importantes;
- mudança de provider estratégico;
- mudança de ownership entre Core/provider/app;
- alteração de política de custo/privacidade;
- mudança de roadmap que possa parecer “feature removida”.

Cada ADR deve registrar:

```text
Status
Data
Contexto / problema
Método anterior (se houver)
Evidência do problema
Alternativas consideradas
Decisão
Motivos
Consequências
Critério para revisitar
ADRs superseded relacionados
```

Status permitido:

- `PROPOSED`;
- `ACCEPTED`;
- `REJECTED`;
- `SUPERSEDED`;
- `DEPRECATED`.

Um ADR antigo permanece no repositório quando é substituído; seu status muda para `SUPERSEDED` e aponta para o novo.

---

## 4. Regra de roadmap

Não concluir que uma feature foi cancelada só porque não aparece na versão atual.

Toda feature relevante movida deve registrar:

```text
origin_version
current_target
status
movement_reason
```

Status sugerido:

- IMPLEMENTED;
- IN_PROGRESS;
- PLANNED;
- MOVED;
- DEFERRED;
- EXPERIMENTAL;
- REJECTED.

Exemplo obrigatório de interpretação:

```text
planejamento antigo V0.9
→ parte de corpo/percepção foi movida para V1.1
→ parte de estética/autoimagem foi movida para V1.2
→ MOVED != CANCELLED
```

---

## 5. Filosofia arquitetural

Sempre preferir:

```text
estado persistente > prompt gigante
algoritmo local > request remoto desnecessário
módulo determinístico > agente desnecessário
evento estruturado > texto solto
representação canônica > interpretação repetida
causalidade > aleatoriedade
continuidade > espetáculo
uma interface estável > acoplamento ao provider
fallback explícito > troca silenciosa de identidade
benchmark > opinião sobre tecnologia
```

---

## 6. Invariantes da Amora

Não violar silenciosamente:

1. A Amora não é um LLM.
2. Provider não é identidade.
3. Estado canônico vive fora do provider.
4. Trocar provider não troca relação, memória, personalidade ou self-facts.
5. Memória ambígua não vira fato sem provenance/confidence.
6. Speaker ID não é autenticação.
7. Pessoa, dispositivo e sessão são entidades distintas.
8. Emoções preservam causa/alvo quando aplicável.
9. Relações mudam lentamente.
10. Traits mudam ainda mais lentamente.
11. Silêncio é comportamento válido.
12. Nem toda fala capturada é dirigida à Amora.
13. Nem toda percepção exige LLM.
14. AUTH nunca entra em memória cognitiva/provider/TTS.
15. Disclosure e remote processing são decisões diferentes.
16. Event Bus deve suportar dedupe/idempotência.
17. Estado persistente usa migrations.
18. Backup/restore deve ser verificável.
19. Falha de plugin não derruba Core.
20. Toda ação importante observa resultado.
21. Naturalidade não deve depender exclusivamente de prompt.
22. Semântica social deve pertencer ao Core sempre que possível.
23. Voz normal deve manter uma identidade consistente e pt-BR.
24. A arquitetura não pode depender de um free tier específico.

---

## 7. Controle de escopo

Antes de implementar ideia nova, responder:

- já existe módulo equivalente?
- já houve tentativa parecida?
- existe ADR sobre isso?
- qual versão realmente deve receber isso?
- é requisito ou exploração?
- aumenta custo remoto?
- duplica sistema existente?
- altera schema persistente?
- altera identidade/comportamento?
- exige certificação física?

Não transformar automaticamente uma ideia boa em requisito da V1.

Categorias possíveis:

```text
V1
V1.1
V1.2
V2
EXPERIMENTAL
BACKLOG
REJECTED
```

---

## 8. Regra contra “patch loop”

Se a mesma área gerar erros repetidos ou vários remendos seguidos, **parar de empilhar patches**.

Sinais de loop:

- três ou mais correções próximas no mesmo caminho sem estabilizar;
- fallback corrigindo sintoma de outro fallback;
- quota/provider definindo comportamento central;
- exceções/blacklists crescendo para compensar arquitetura;
- cada correção cria outro estado especial;
- não está claro qual módulo é dono da decisão;
- muita alteração sem melhora proporcional na experiência física.

Quando isso ocorrer:

1. parar expansão da feature;
2. reconstruir o fluxo ponta a ponta;
3. identificar ownership errado;
4. pesquisar soluções existentes;
5. comparar alternativas;
6. escolher arquitetura substituível;
7. registrar ADR;
8. implementar um caminho limpo;
9. certificar antes de prosseguir.

Não gastar tokens/tempo indefinidamente repetindo a mesma abordagem que já demonstrou falha estrutural.

---

## 9. Pesquisa técnica obrigatória

Pesquisar antes de decidir quando o assunto depender de informação mutável ou especializada, como:

- APIs;
- free tiers;
- quotas;
- modelos;
- Android/iOS;
- runtime neural;
- licença de dataset/modelo;
- benchmarks de mobile;
- provider;
- SDK;
- compatibilidade;
- TTS/STT;
- formatos multimodais.

Prioridade de fonte:

1. documentação oficial;
2. papers;
3. repositório oficial;
4. model cards/licenças oficiais;
5. benchmarks reproduzíveis;
6. comunidade técnica como evidência complementar.

Separar explicitamente:

```text
FATO CONFIRMADO
HIPÓTESE
RECOMENDAÇÃO
EXPERIMENTO
```

Não apresentar suposição sobre sistema de terceiros como fato.

---

## 10. Política de custo

Objetivo:

```text
R$ 0 sempre que praticável
```

Antes de enviar tarefa para provider remoto, perguntar:

> Pode ser resolvido localmente por regra, estado, índice, cache, OCR, plugin, heurística, modelo local leve ou resultado já conhecido?

Se sim, preferir local.

Para qualquer provider remoto:

- abstrair interface;
- monitorar quota;
- ter cooldown/backoff;
- ter fallback;
- registrar uso;
- reservar quota quando aplicável;
- permitir modo degradado;
- evitar transformar “grátis hoje” em dependência arquitetural.

APIs pagas não são requisito operacional padrão da Amora.

---

## 11. Providers

Provider é aprovado por **task class**, não por fama.

Usar Cognitive Replay/contexto congelado para comparar candidatos.

Avaliar:

- pt-BR;
- aderência ao DialoguePlan;
- personalidade;
- continuidade;
- perspectiva;
- entities;
- structured output;
- tool calling;
- latência;
- quota;
- custo;
- estabilidade.

Direção atual:

- Groq: principal enquanto aprovado;
- Cloudflare Workers AI: provider planejado da malha gratuita;
- OpenRouter: specialist broker/último recurso;
- Gemini: tarefa específica, não dependência de identidade/voz normal;
- provider novo: precisa de certificação antes de promoção.

---

## 12. Diálogo

Fluxo de ownership:

```text
Core
↓
DialoguePlan
↓
provider como surface realization
↓
validator estrutural
↓
saída
```

Não resolver comportamento genérico de chatbot com blacklist crescente.

Se o provider insiste em comportamento incompatível com o plano, considerar:

- enriquecer semântica local;
- adicionar stance/closing_mode/relational attitude;
- validator estrutural;
- trocar modelo após benchmark;
- fine-tune/RP-capable model no futuro se necessário.

O provider não ganha autoridade sobre self-facts.

---

## 13. Voz

### 13.1 Identidade

Voz faz parte da identidade percebida.

Não alternar silenciosamente entre timbres muito diferentes durante uso normal.

Locale canônico: **pt-BR**.

### 13.2 Caminho atual

Objetivo V1:

```text
VoiceIdentity
↓
Prosody Planner
↓
sherpa-onnx + TTS neural local pt-BR
↓
PCM
↓
AudioTrack
```

Gemini TTS não é caminho normal.

Android TTS nativo é emergência/bootstrap.

Uma futura voz própria precisa de material com direitos claros; não derivar voz de pessoa/modelo cuja licença proíba derivados.

### 13.3 Benchmark físico

Não declarar voz local pronta apenas porque CI compila.

No Galaxy A15 medir pelo menos:

- download/ativação do pack;
- checksum;
- primeira latência;
- RTF;
- RAM;
- CPU;
- temperatura;
- estabilidade longa;
- pt-BR real;
- pronúncia de Amora/BK/gírias;
- barge-in;
- funcionamento offline;
- ausência de chamadas TTS remotas.

---

## 14. Código

Ao implementar:

- modularizar comportamento repetido;
- usar type hints/schemas;
- evitar abstração vazia;
- evitar números mágicos;
- manter interfaces substituíveis;
- criar testes;
- manter logging estruturado;
- registrar estado observável;
- fazer migrations para persistência;
- não deixar código morto/intencionalmente duplicado;
- remover caminhos superseded quando já não forem necessários, preservando o motivo no ADR/Git.

Python:

- asyncio quando apropriado;
- dataclasses/Pydantic;
- SQLite;
- migrations;
- testes determinísticos.

Android:

- Kotlin;
- coroutines/Flow quando apropriado;
- APIs Android modernas;
- bridge explícita Core ↔ app;
- lifecycle/cancellation corretos.

---

## 15. Observabilidade

Toda decisão estrutural importante deve ser explicável por estados/códigos do próprio sistema, sem depender de chain-of-thought privado.

Expor quando útil:

- state;
- reason codes;
- DialogueAct;
- intention;
- provider/model;
- quota/cooldown;
- plugin health;
- action result;
- speech state;
- error summary.

Console humano deve atualizar a interface sem spam de polling repetitivo. Logs estruturados completos ficam separados.

---

## 16. Testes

Manter regressões para categorias existentes, incluindo:

### Entidades

- BK/Bad King/Slay/Nick = mesma pessoa.

### Perspectiva

- eu/você;
- relações familiares;
- discurso citado;
- múltiplos speakers.

### Emoção

- afeto por entidade;
- spillover;
- reparação;
- ambivalência;
- decay;
- dedupe.

### Social

- fala para Amora;
- fala para terceiro;
- conversation floor;
- continuidade que não se auto-renova indefinidamente.

### Diálogo

- self-fact known/missing;
- disclosure sem conselho não pedido;
- support request explícito;
- opinião/stance;
- `ask_followup=false`;
- `offer_help=false`;
- ausência de invenção de fatos.

### Voz

- local pack;
- checksum;
- fallback explícito;
- barge-in;
- cancellation;
- offline;
- nenhuma troca silenciosa de voz normal.

### Persistência

- migration;
- restart;
- backup;
- restore;
- integrity.

### Providers

- failover antes da fala;
- não costurar providers depois de fala iniciada;
- quota/cooldown;
- replay sem mutação.

---

## 17. Certificação física

CI prova integração/compilação/testes automatizados. **CI não prova comportamento físico no Galaxy A15.**

Quando a feature depende de áudio, Android lifecycle, temperatura, microfone, speaker, rede, storage ou performance, precisa de validação física.

Marcar separadamente:

```text
TECHNICALLY IMPLEMENTED
PHYSICALLY CERTIFIED
```

Nunca confundir os dois.

---

## 18. Processo de mudança grande

```text
1. ler canon + ADRs relevantes
2. identificar ownership
3. pesquisar se necessário
4. definir critério de sucesso
5. avaliar impacto de persistência/privacidade/custo
6. criar branch/PR
7. implementar
8. criar/atualizar testes
9. rodar CI
10. validar fisicamente quando necessário
11. atualizar canon/roadmap/ADR
12. só então fechar etapa
```

Se o repositório exigir PR para `main`, respeitar a regra; não tentar burlar proteção.

---

## 19. Sequência atual de fechamento da V1

Não abrir quatro frentes simultaneamente.

### Passo 1 — voz local estável

Objetivo: Amora falar local/offline com timbre consistente pt-BR, sem Gemini no caminho normal.

### Passo 2 — identidade de diálogo

Objetivo: Core possuir atos/stance/closing behavior suficientes para reduzir comportamento de chatbot; validator e replay comparativo.

### Passo 3 — malha gratuita de providers

Objetivo: Groq + Cloudflare, com OpenRouter como especialista/último recurso e failover/quota certificados.

### Passo 4 — voz canônica + certificação V1

Objetivo: promover voz própria quando viável e executar certificação longa da V1.

**Não iniciar o passo seguinte enquanto houver defeito estrutural aberto no anterior.**

---

## 20. Pergunta final de revisão

Antes de aceitar arquitetura/feature, perguntar:

> **Isso ajuda a Amora a continuar sendo a mesma Amora, de forma natural, sustentável, gratuita sempre que praticável, substituível, observável e compreensível ao longo do tempo?**

Se não, reavaliar.
