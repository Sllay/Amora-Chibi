# ADR-0005 — Dividir o escopo antigo V0.9/V0.95 entre V1.1 e V1.2

**Status:** ACCEPTED  
**Data:** 2026-10-10  
**Supersedes:** roadmap antigo que concentrava avatar/customização em V0.9 e percepção avançada em V0.95  
**Superseded by:** none

## Contexto

O planejamento inicial colocava avatar, rig, física, expressões e customização em V0.9, seguido por percepção avançada em V0.95.

Com a evolução do produto ficou claro que havia pelo menos três domínios diferentes:

1. fechar cognição, diálogo, voz e estabilidade da V1;
2. percepção visual + corpo/avatar final;
3. preferência estética e autoimagem persistente.

Manter tudo no fechamento V1 aumentaria escopo e dificultaria certificação.

## Decisão

O fechamento V1.0 permanece focado em cognição/diálogo/voz/providers/Android/estabilidade.

Escopo antigo é redistribuído:

### V1.1 — percepção/corpo

- percepção visual;
- screen mapping;
- corpo/avatar final;
- renderer/rig/expressões necessários;
- integração semântica Mind → Avatar.

### V1.2 — estética/autoimagem

- desejo estético persistente;
- escolhas de aparência;
- avaliação de roupa;
- preferências visuais;
- propriedade sobre a própria autoimagem.

## Motivos

Percepção/corpo é problema técnico diferente de gosto/autoimagem persistente.

A V1 precisa fechar a capacidade de “ser a mesma Amora” antes de expandir corpo e estética.

## Consequências

Features antigas de V0.9/V0.95 não devem ser consideradas esquecidas.

Em chats futuros, a ausência desses itens na V1 significa **MOVED**, não `REJECTED`.

O roadmap deve registrar a linhagem para impedir reintrodução acidental no escopo errado.

## Critério para revisitar

Revisitar apenas se dependência técnica real exigir antecipar parte pequena de V1.1/V1.2 para fechar a V1. Nesse caso, antecipar somente a infraestrutura mínima necessária, não todo o escopo futuro.
