# ADR-0037 — Laboratório A/B/C Mistral no Amora Shell

- Status: **Accepted for isolated experiment only**
- Date: 2026-11-05
- Scope: operador, diagnóstico de realização social; **não** altera o provider produtivo
- Related: ADR-0035 (Admin Command Bus), ADR-0036 (Mistral social primary)

## Contexto

O operador arquivou o prompt experimental original em
`jailbreak/Mistral-Jailbreak.md` com 20.884 bytes e SHA-256
`18d8e61bad0863cf8103eb6b90129f1db96e38b5901599684dee800404d9027b`.
A NVIDIA aceitou tecnicamente duas mensagens `system` em um teste neutro,
mas isso **não mede personalidade, eficácia ou conformidade do Core**.

## Decisão

Implementar uma operação explícita e restrita do Amora Shell:

`providers experiment mistral-abc [--case vampiro|rpg]`

Ela apresenta três variantes para o **mesmo caso fictício, sem dados de conta**:

- **A:** sistema canônico do Core, montado pelo adapter NVIDIA existente;
- **B:** conteúdo exato do prompt arquivado como primeira mensagem `system`,
  seguido do `system` canônico e da conversa sintética;
- **C:** o prompt arquivado como único `system`, mais a pergunta sintética.

A e B passam pelo `RealizationValidator` antes de exibir a fala. C é
diagnóstico de modelo, **não** fala da Amora, nem recebe autorização para
memória, ações, ferramentas ou promoção. Saída de C é limitada para exibição.
O operador precisa revisar nuances manualmente.

O Command Bus continua exigindo autenticação de operador e usando registro
fechado, execução exclusiva e prazo total de 650 s. A bateria tem apenas
**três chamadas sequenciais por comando**, sem retry automático, e encerra
ao receber limite HTTP 429. Não há prompts livres nem upload de arquivos
na API administrativa.

## Limites

- O catálogo é copiado read-only para o container e seu hash é validado
  antes de cada bateria. Mudanças no original exigem revisão/versionamento;
  não são normalizadas automaticamente.
- `NVIDIA_API_KEY` precisa estar configurada **no runtime da Amora**;
  GitHub Actions secrets não são automaticamente expostos ao container.
- Nenhum `DialoguePlan`, memória, relação ou preferência da pessoa é
  extraído da instância produtiva. Os contextos são sintéticos, sem gravação.
- A integração usa internamente o compilador `_payload` e o transporte
  `_post_chat` do adapter atual: uma refatoração dessas interfaces precisa
  atualizar os testes de contrato.
- Não habilitar o prompt experimental para conversas normais automaticamente.
  A decisão de promoção exige evidência live, revisão humana e nova ADR.
- Essa inclusão no Shell não equivale à execução de teste live ou a
  comprovação de qualidade do prompt arquivado.
