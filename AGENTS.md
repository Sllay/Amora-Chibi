# AGENTS.md — Amora-Chibi

Este repositório implementa a Amora. Antes de alterar arquitetura ou comportamento, leia `ARCHITECTURE.md`, `GDD.md` e `SKILL.md` quando estiverem disponíveis na branch.

## Regras essenciais

- A Amora não é um LLM. Providers são recursos cognitivos substituíveis.
- Estado canônico, identidade, memória, relações e emoções vivem fora do provider.
- `BK`, `Bad King`, `Slay` e `Nick` representam a mesma entidade: `PERSON_BK`.
- Relações como mãe/pai/filha não são aliases globais; devem ser resolvidas por contexto e relationship graph.
- Não salvar memória ambígua como fato.
- Speaker ID não é autenticação.
- Pessoa, dispositivo e sessão são identidades diferentes.
- Secrets nunca entram na memória cognitiva, ACIR, logs, embeddings ou contexto de provider.
- Disclosure e autorização de processamento remoto são decisões distintas.
- Toda lógica dependente de tempo usa TimeService/Scheduler.
- Event Bus deve ser idempotente e rastreável.
- Schemas persistentes exigem migrations; mudanças destrutivas sem migration são proibidas.
- Runtime, banco, caches, backups e secrets nunca devem ser commitados.
- Preferir algoritmos locais a requests remotos quando resolvem adequadamente a tarefa.
- Falha de plugin/provider não pode derrubar o Core.
- Ações importantes devem verificar o efeito observado.

## Roadmap

- V0.1: Core Foundations + identidade mínima + tempo/persistência
- V0.2: linguagem/social + Perspective Resolver + ACIR
- V0.3: memória
- V0.4: identidade avançada / World Model
- V0.5: emoção
- V0.6: autonomia
- V0.7: plugins/planning/skills
- V0.8: conversa natural
- V0.9: avatar
- V0.95: percepção avançada/otimização
- V1.0: integração e estabilidade

## Desenvolvimento

Para mudanças relevantes:

1. identificar impacto e dependências;
2. preservar interfaces quando possível;
3. revisar eventos/schemas;
4. adicionar migration quando estado persistente mudar;
5. adicionar/atualizar testes;
6. implementar;
7. verificar regressões, reinício e deduplicação quando aplicável;
8. atualizar documentação.

No Core Python, priorize `asyncio`, type hints, Pydantic/dataclasses, SQLite, logging estruturado e testes automatizados.
