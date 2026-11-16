# MM.00D.3 — Admissão autorizada, ordenação e recuperação

Status: planejamento antes do código. Dependência: #240 (D.2) validado e integrado. Acompanhamento #238.
Não ativar captura, provider ou storage novo neste checkpoint sem contrato e testes.

## Contratos existentes inspecionados
- `core/amora/identity/session.py`: SessionService.get, SessionIdentity.is_active, authenticated_person_id, device capabilities, expires_at/closed_at. Sessão autenticada não equivale a consentimento de captura.
- `core/amora/identity/profiles.py`: PersonProfileService.resolve_actor e verified Discord binding; identifica por ID nativo, platform e account_scope, não nome de exibição.
- `core/amora/messaging/session_identity.py`: messaging_session_ref, escopo de conversa canônico; `attention.resolve_speaker` usa bindings e não inventa ator.
- `core/amora/messaging/reflection_queue.py`: jobs duráveis referem input em conversation_messages, requerem platform_messaging/remote processing, possuem lease/CAS/reconcile. Não reutilizar fingindo que visão é fala.
- `core/amora/events/bus.py`: persistência antecede handlers; replay não reexecuta handlers. Recuperação de consolidação exige unidade durável separada de entrega de evento.
- D.1: event store imutável e leitura por ID. D.2: candidato episódico e create-only; seleção explícita, replay por conjunto exato de IDs.

## Checkpoints antes de MM.01

| ID | Entrega | Gate | Estado |
|---|---|---|---|
| D.3A | Autoridade interna de sessão/stream | Core resolve ator; consentimento, expiração, revogação, audiência, escopo, revalidação; sem confiança em PERSON_* vindo do transporte | Planejado |
| D.3B | Buffer temporal limitado | epoch/sequence, reorder, gap, backpressure, limites por bytes/idade/contagem; eventos tardios não substituem presente | Planejado |
| D.3C | Seleção e recuperação durável | somente evidências relevantes; plano de trabalho por IDs, dedupe, lease/reconcile; replay antes/depois de falha; sem mídia bruta | Planejado |
| D.3D | Primeiro adaptador interno CH2 e teste do ciclo | validação de identidade e mídia, memória, recall, reinício, falhas, privacidade, métricas; gate real Discord/Groq em MM.01 | Planejado |

## Decisões de segurança e desempenho

1. D.3A começa com um modelo interno emitido pelo Core. Revalidar sessão/device/binding de origem e consentimento antes de admitir conteúdo, antes de provider remoto e antes de persistir. Escopo e audience não podem ser ampliados pelo worker. Revogação/expiração bloqueiam novas admissões; já persistido segue política de retenção/remoção separada. Não assumir identidade só porque um usuário é membro de uma call.
2. D.3B mantém dados brutos somente em buffer efêmero, com limite configurado e descarte explícito. Sequência por stream/epoch; reconexão não reaproveita epoch antigo. Tempo de captura é separado de chegada e não autoriza ordenação entre relógios não sincronizados. Métricas sem conteúdo pessoal.
3. D.3C não automatiza toda observação. Antes de persistência: política, relevância, custo, dedupe, limite por sessão e janela. Não marcar como durável uma fila em memória. Avaliar contrato de job já existente; se nenhum suportar referências a observações sem falsificar proveniência, documentar ADR e migração isolada com recuperação PCS antes de criar tabela. Não alterar produção sem gate.
4. D.3D começar por imagem/sticker pontual existente, não pela call ou tela. Provar entrada -> cognição -> resposta -> memória -> recall; não duplicar CPF como episódio. Não confundir enriquecimento visual assíncrono com interpretação disponível na mesma resposta.
5. Testes físicos (iPhone, EveryCord + call, Discord DAVE, providers) continuam em MM.01–05. O contrato base não comprova capacidade real de captura.

## Registro de evidência
Antes de cada subcheckpoint atualizar este ledger e #238. Após implementação: SHA, PR, CI exato, resultado e limites. #240 ainda em validação quando este plano foi escrito. Sem alterações de runtime nesta branch até o gate.
