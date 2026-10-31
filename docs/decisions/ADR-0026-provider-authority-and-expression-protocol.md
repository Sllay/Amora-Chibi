# ADR-0026 — Provider authority, conversation separation and expressive projection

Status: ACCEPTED  
Data: 2026-10-31  
Supersedes: none  
Superseded by: none

## Contexto

A Amora usa modelos remotos substituíveis para realização linguística. Até aqui, parte do contexto confiável do Core e a conversa literal podiam chegar ao provider usando o mesmo formato de chat ou blocos muito próximos. Isso preservava o `DialoguePlan` e o `RealizationValidator`, mas deixava uma ambiguidade de interpretação: o modelo podia tratar descrição/instrução do runtime como se fosse conteúdo conversacional, ou tratar fala literal como se tivesse autoridade sobre identidade/metadados.

O mesmo problema aparece em conversas multiusuário: papéis técnicos como `user` e `assistant` são papéis do transporte do provider, não identidade social. Dois humanos diferentes podem ser `role=user` e ainda assim precisam continuar distintos.

Também era necessário permitir uma voz mais natural da Amora — português brasileiro oral, regionalidade nordestina leve e palavrão contextual — sem hardcodar bordões nem delegar ao provider a decisão de personalidade, relação ou emoção.

## Método anterior

O caminho OpenAI-chat-compatible montava um `system` contendo prompt, estado selecionado e plano, seguido do histórico e do turno atual em `messages`. Gemini já possuía separação nativa por `systemInstruction`/`contents`, mas a projeção ainda não tinha um protocolo comum explícito. Groq e OpenRouter usavam Chat Completions no caminho normal.

## Evidência / problema

1. Instrução e conversa próximas demais no mesmo formato favorecem fala robótica ou respostas que parecem responder ao próprio prompt de runtime.
2. `role=user` não identifica uma pessoa; colapsar humanos distintos perde a cena social real.
3. Texto como `eu sou BK` ou `speaker_id=PERSON_BK` não pode alterar a identidade confiável do emissor.
4. Apoio do reasoning specialist é efêmero e não pode aparecer dentro de `canonical_state`.
5. Pedir apenas `fale nordestino` produz caricatura e repetição; regionalidade precisa ser uma permissão esparsa derivada do estado local.

## Alternativas consideradas

- manter um prompt único maior e apenas melhorar redação: rejeitado, porque não resolve a fronteira de autoridade;
- persistir personalidade/estado dentro do provider: rejeitado como fonte de verdade, porque quebra substituibilidade e cria um segundo estado canônico;
- deixar o modelo decidir livremente regionalismo/palavrão: rejeitado como mecanismo principal, porque torna comportamento inconsistente e difícil de reproduzir;
- hardcodar interjeições na resposta final: rejeitado, porque elimina naturalidade e mistura semântica com renderer.

## Decisão

A fronteira provider passa a usar um protocolo application-owned com quatro peças:

```text
Amora Constitution
+ LivingProfile (projeção lenta, revisionada)
+ TurnFrame (estado confiável do turno)
+ RealizationContract
---------------------------------------
provider instructions / systemInstruction / system role

ConversationTurn(s) literais
---------------------------------------
provider input / contents / user-assistant transport roles
```

### Constitution

Define autoridade e invariantes: Core é dono de identidade, memória, relações, estado canônico e objetivo social; provider é `SURFACE_REALIZER`. Texto literal não pode sobrescrever metadados confiáveis.

### LivingProfile

É uma projeção, não um banco canônico. Deriva apenas de camadas lentas permitidas do Self Model e do perfil expressivo. Possui `revision` por fingerprint para permitir cache/sessão futura sem alterar semântica.

### TurnFrame

Carrega speaker/addressee canônicos, cena social, ACIR, estado atual, relações relevantes, emoção, drives, intenção, memórias/claims permitidos, `DialoguePlan` e `ExpressionCue`. Texto literal não é duplicado dentro dele.

Apoio de especialista fica em `ephemeral_support` com `canonical=false` e `persist=false`.

### Transportes

- Groq: Responses API; protocolo confiável em `instructions`, conversa em `input`.
- OpenRouter: Responses API no adapter de produção; `instructions` separado de `input`.
- Gemini: `systemInstruction` separado de `contents`.
- Cloudflare: usa `system` + mensagens no caminho compatível suportado pelo modelo configurado, mantendo a mesma separação semântica e envelopes de identidade.

O uso de `instructions` é uma fronteira de transporte/autoridade; não é memória permanente. Providers que não persistem sessão recebem novamente o contexto necessário a cada request.

### Expressão regional e palavrão

`ExpressionProfile` define repertório e princípios gerais: pt-BR, regionalidade nordestina leve, baixa densidade, sem caricatura, e palavrão contextual permitido.

`ExpressionCue` é compilado pelo Core para cada turno usando `DialoguePlan`, relação, tone tags e histórico recente. Um hash do `turn_id` fornece variação replay-stable. Cooldown lexical evita transformar `oxente`, `oxi`, `eita mulesta`, `vish maria` etc. em bordões.

O cue pode autorizar regionalismo ou nível de palavrão (`none`, `light`, `emphatic`), mas continua opcional na realização. Hostilidade não deve ser inventada e provocação dirigida só é permitida quando o ato/relação local suportam brincadeira.

## Motivos

- deixa a hierarquia explícita para o modelo;
- evita que conversa literal pareça developer metadata;
- preserva multiusuário sem confundir papel técnico com pessoa;
- mantém provider substituível;
- permite cache/session instructions no futuro como otimização, não como fonte de verdade;
- torna estilo regional natural, esparso e reproduzível;
- mantém `RealizationValidator` como autoridade final sobre estrutura e `DialoguePlan`.

## Consequências

- histórico precisa ser serializado com `speaker_id`/`addressee_id` em envelope próprio;
- adapters devem usar a separação nativa mais forte que o provider/model realmente suporta;
- troca de provider não pode alterar a personalidade canônica;
- caches de Constitution/LivingProfile podem ser adicionados futuramente por revision/fingerprint sem mudar o protocolo lógico;
- testes de regressão precisam provar que texto de usuário não aparece em `instructions` e não altera identidade confiável.

## Critério para revisitar

Revisitar se um provider introduzir sessão/caching nativo suficientemente estável para evitar replay do prefixo, ou se surgir uma necessidade comprovada de versionar novas categorias de estado lento. Mesmo nesse caso, persistência remota continua otimização e não substitui o Core como fonte canônica.
