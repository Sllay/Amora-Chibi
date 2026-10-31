# ADR-0025 — Aprendizagem relacional local por corroboração

Status: ACCEPTED
Data: 2026-10-31
Supersedes: none
Superseded by: none

## Contexto

O remapeamento estrutural de 2026-10-31 confirmou que a Amora já possuía `RelationshipEvolutionService`, Relationship Graph persistente, estado relacional e reflexão cognitiva estruturada, mas faltava fechar o ciclo normal entre evidência conversacional e evolução automática de relação.

Ao mesmo tempo, a reflexão estruturada pode usar providers remotos. Portanto, aceitar diretamente uma afirmação produzida por modelo como relação canônica faria o provider ganhar autoridade sobre identidade social — contrariando a arquitetura local da Amora.

Também existe uma distinção importante entre:

- **relação estrutural/semântica** — por exemplo `father_of`, `mother_of`, `child_of`;
- **afeto dirigido** — affection, trust, anger, irritation, fear, gratitude etc., que pertence ao Emotion Engine.

Essas duas camadas não devem ser colapsadas.

## Método anterior

O estado inicial de relações podia ser persistido e revisado manualmente pelo `RelationshipEvolutionService`, e a cognição podia produzir hipóteses no World Model.

Não existia, porém, um consumidor normal que transformasse evidência relacional repetida em uma revisão temporal da Relationship Graph.

A flag `relationship_learning=false` usada em turnos técnicos/testes não controlava um pipeline completo de learning porque esse pipeline ainda não existia.

## Evidência / problema

Uma única saída de provider não é evidência suficiente para alterar uma relação canônica.

Também não é seguro contar repetições do mesmo evento como corroboração independente, aceitar IDs de entidade inventados pelo provider, aprender relações entre terceiros fora da cena social atual ou deixar uma mensagem gerada pela própria Amora reabrir um turno cujo input desabilitou `relationship_learning`.

## Alternativas consideradas

### 1. Provider escreve diretamente na Relationship Graph

Rejeitada. Dá autoridade social ao modelo remoto e permite alucinação virar identidade persistente.

### 2. Toda hipótese relacional aceita pelo World Model vira relação imediatamente

Rejeitada. Uma única inferência ainda é insuficiente e mistura hipótese epistemológica com identidade temporal consolidada.

### 3. Aprendizagem local por corroboração de eventos independentes

Aceita. Mantém o provider como fonte de propostas, enquanto a decisão de consolidação pertence ao Core.

## Decisão

A aprendizagem relacional automática segue estas regras:

1. o provider pode produzir apenas uma proposta estruturada;
2. a proposta passa pelos gates cognitivos locais e pela consolidação de perspectiva/ACIR aplicável;
3. a relação só é considerada a partir de `WorldClaim` persistido como `HYPOTHESIS` não canônica com source `cognition:*`;
4. subject e object relacionais devem ser entidades canônicas registradas;
5. os dois endpoints devem pertencer ao par social do **input observado** daquele turno;
6. apenas a mensagem de entrada `stt`/`text` pode autorizar learning; saída cognitiva/TTS da própria Amora nunca substitui esse input;
7. `test_mode=true` ou `relationship_learning=false` no input bloqueiam aprendizado relacional;
8. são necessários pelo menos **2 IDs de evento observados distintos** dentro da validade das hipóteses;
9. a confiança média das evidências deve ser pelo menos **0.78**;
10. o estado aprendido não pode rebaixar uma relação existente de confiança maior ou igual;
11. a revisão temporal persiste provenance, IDs dos eventos, IDs das hipóteses e confiança média;
12. relações temporais ativas aprendidas são reidratadas no `EntityRegistry` durante bootstrap;
13. inicialmente, o conjunto automático fica limitado a `father_of`, `mother_of` e `child_of` até que outros predicados recebam semântica e testes equivalentes;
14. directed affect continua pertencendo ao Emotion Engine e não é convertido em Relationship Graph estrutural.

## Motivos

- preserva autoridade social no Core;
- transforma `relationship_learning=false` em gate real;
- exige causalidade/provenance em vez de confiança cega no provider;
- impede duplicação do mesmo evento de inflar evidência;
- mantém Relationship Graph e Emotion Engine com ownership distintos;
- permite evolução automática sem abrir uma porta ampla para alucinação persistente.

## Consequências

A aprendizagem é deliberadamente conservadora. Relações de terceiros, predicados ainda não reconhecidos ou afirmações sem corroboração permanecem hipóteses e não viram identidade estrutural.

Expandir o conjunto de relações aprendíveis exige definir semântica local, evidência mínima, comportamento de revisão e regressões correspondentes.

Falhas no learner são observáveis, mas ocorrem depois da persistência válida do World Model e não podem transformar uma reflexão válida em falha de provider nem desfazer um claim já aceito pelos gates anteriores.

## Critério para revisitar

Revisitar quando existir pelo menos uma destas condições:

- novo conjunto de predicados relacionais com semântica canônica e testes próprios;
- evidência empírica de que 2 eventos / confiança média 0.78 são excessivamente rígidos ou permissivos;
- parser semântico local mais rico capaz de ancorar terceiros com segurança;
- nova política explícita para aprendizagem relacional multiusuário ou multissessão.
