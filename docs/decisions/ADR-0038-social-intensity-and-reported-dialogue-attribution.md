# ADR-0038 — Intensidade social contextual e autoria de perguntas citadas

**Status:** ACCEPTED  
**Data:** 2026-11-07  
**Escopo:** Cognição social/Core; `DialogueOrchestrator`, `ExpressionProfile` e `ExpressionCue`.  
**Supersedes:** nenhum; complementa ADR-0026 e ADR-0036.

## Contexto / problema

Nos experimentos sintéticos `amora-v2-v3` com Mistral Large 4, cenas
noturnas adultas e consensuais foram classificadas como
`TEASE` com o objetivo literal *"return light playful energy"* e o
move `light_tease`, mesmo quando a conversa continha palavrões,
provocações francas e malícia contextual. Um overlay V3 mais desinibido
não eliminou a limitação semântica vinda do Core.

Outra falha observável: o texto sintético com `Rui ...: 'Tu tá ...?'`
foi classificado como `ANSWER_QUESTION` para a Amora, apesar de a
pergunta ser uma fala citada **de Rui para Lia**. O classificador
buscava `?` em `context.user_text` inteiro, sem autoria de discurso
citado. Isso troca o ato da Amora e reduz naturalidade.

## Métodos anteriores

1. Modo `TEASE` orientado sempre a provocação leve, inclusive em
   resenha explícita; sem noção transitória de intensidade social.
2. Regex de perguntas aplicada a todo o `user_text`, incluindo
   perguntas de personagens citados, em vez de somente ao conteúdo
   dirigido ao interlocutor atual.
3. Aumentar apenas a temperatura do provider como hipótese de resolver
   falta de espontaneidade.

## Alternativas

- **Ampliar indefinidamente o prompt V3:** rejeitado como solução
  estrutural. O Core continuaria ditando `light_tease`.
- **Deixar o provider decidir identidade, relação ou autorização:** rejeitado.
- **Adicionar blacklist ou allowlist de palavrões:** rejeitado; não é o
  mecanismo necessário nem a política de privacidade.
- **Usar atribuição local de discurso citado e planos expressivos por
  contexto:** aceito; mantém ownership canônico, é reproduzível e
  permite testes sem custo remoto.

## Decisão

1. **Atribuição de discurso relatado.** Quando há sinais explícitos
   de terceiro citado (pessoa/verbos de fala + trecho entre aspas),
   o classificador local usa uma versão somente para roteamento em
   que o trecho citado não conta como pedido/pergunta/afeto direto à
   Amora. `context.user_text` original, histórico, speaker IDs
   e contexto enviado ao provider permanecem inalterados.
2. **Sem mudança de floor/speech authorization.** O sistema não cria
   um turno novo; reage apenas quando o Core já autorizou o turno.
   Em mensagens multiusuário reais, speaker/addressee/floor confiáveis
   seguem prioritários. A heurística atende sobretudo descrições
   sintéticas de cena, sem pretender resolver atribuição arbitrária.
3. **Intensidade contextual em resenha adulta.** Se a cena já contém
   brincadeira e drive de brincar adequado, o Core pode planejar
   `bold_tease` sem impor o teto `light_tease`. O objetivo prioriza
   humor espontâneo, sujo ou ácido quando pertinente, não exibição
   programada de obscenidade. Em cenas leves usa `contextual_tease`.
4. **ExpressionCue é orientação, não filtro.** O nível verbal `bold`
   informa o realizador, sem criar direito de falar, inventar
   observações, agredir terceiros ou sexualizar a relação pai–filha.
   O perfil da Amora favorece expressão espontaneamente atrevida nos
   turnos sociais adequados, inclusive sem ordem explícita de xingar.
5. **Sem afrouxar regras estruturais.** `RealizationValidator`,
   consentimento, privacidade, `self_choices`, identidade, segurança,
   tool/ActionBus e limites de perguntas seguem iguais. Em apoio,
   tristeza ou tarefas reais, a intensidade social não força piadas.

Não foi adicionada memória persistente nem migration. A mudança é
semântica por turno, testada com cenas sintéticas e replay.

## Motivos

- Separar **a autoria da pergunta** da **moldura da cena**.
- Corrigir o motivo estrutural para superficialidade sem transformar
  provedor em autoridade moral/social.
- Dar espaço à característica naturalmente safada da personagem sem
  impor linguagem gráfica em qualquer situação.
- Registrar reason codes que expliquem a seleção do ato no Core.

## Consequências

- Mudanças na seleção de `TEASE` e nos nomes de moves são intencionais
  e podem alterar respostas live; devem ser observadas via `DialoguePlan`.
- Heurística de aspas e verbos não entende toda narrativa em português;
  não substitui modelos de cena ou speaker ID.
- Testes offline provam apenas seleção e validação de plano, não a
  qualidade de humor do Mistral; a certificação live permanece distinta.
- Compatibilidade: clientes que apenas leem `response_moves` e os
  validam pelo Core continuam suportados; modelos podem precisar ser
  recertificados quando recebem `bold_tease`.

## Critério para revisitar

Se cenas reais com participantes distintos, áudio e turn manager
apresentarem falsa atribuição, evoluir para uma representação
estruturada de discurso citado/endereçamento no `ACIR` e
`Social Scene`, em vez de acumular regex. Se o Mistral não entender
novos moves, medir em A/B antes de alterar a ontologia ou o schema.
