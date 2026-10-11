# ADR-0001 — Canon no GitHub, ADRs e version lineage

**Status:** ACCEPTED  
**Data:** 2026-10-10  
**Supersedes:** none  
**Superseded by:** none

## Contexto

O projeto Amora cresceu através de conversas longas e documentos anexados ao projeto ChatGPT. Decisões importantes mudaram de versão, alguns métodos foram abandonados e o histórico de chat começou a atingir limites do cliente.

Dependência exclusiva do histórico da conversa cria risco de:

- esquecer por que uma solução foi rejeitada;
- ler documento antigo como se fosse atual;
- tratar feature movida como cancelada;
- reinventar arquitetura já discutida;
- repetir erros em chats novos.

## Método anterior

GDD, Architecture e Skill existiam principalmente como arquivos anexados ao projeto/conversa, enquanto o repositório continha documentação por versão.

## Decisão

O GitHub passa a armazenar o canon da Amora:

- `docs/canon/GDD.md`;
- `docs/canon/ARCHITECTURE.md`;
- `docs/canon/SKILL.md`;
- `docs/roadmap/VERSIONS.md`;
- `docs/decisions/`.

`docs/canon/*` descreve somente o estado vigente.

O histórico Git preserva versões anteriores exatas.

ADRs preservam o motivo das mudanças relevantes.

`VERSIONS.md` preserva origem/destino de features movidas.

Arquivos/instruções mantidos no projeto ChatGPT devem futuramente focar em como o assistente trabalha no projeto, e não competir com o canon técnico da Amora.

## Motivos

- Git acompanha o código real;
- novos chats podem consultar a mesma fonte;
- histórico fica versionado;
- decisões rejeitadas permanecem explicáveis;
- reduz dependência de memória resumida de conversa.

## Consequências

Toda mudança importante consolidada deve atualizar os documentos relevantes.

Documento antigo não é especificação atual por padrão.

Antes de restaurar método antigo, consultar ADRs.

## Critério para revisitar

Revisitar somente se o repositório deixar de ser o local persistente principal do projeto ou surgir um sistema de documentação versionado com garantias superiores e integração equivalente ao Git.
