# ADR-0039 — Fase 2: atribuição, contexto e expressão V3

Status: ACCEPTED for software; remote rollout/certification pending.

Core continua proprietário de identidade, fatos, relações, privacidade,
permissões, ações e persistência. Protocolo e validador não são flexibilizados.

A entrada AUTH/SECRET é redigida antes de inferência/eventos/retorno, e bloqueada
antes de atenção/audit messaging ou cache/STT externo. Registros históricos
existentes não são automaticamente apagados: revisão de retenção é operação
separada que não deve revelar valores nem destruir backups sem política.

Atribuição usa segmentos com autoria/destino, não remoção global de aspas.
Preferências persistentes exigem solicitação aplicável ao sujeito Amora,
evidência vinculada ao turno e revalidação no commit. Disclosure/narrativa
não conferem essas permissões. Ambiguidade pode manter a fala sem conceder
escrita pessoal. Isso não constitui parser geral de PT-BR.

Contexto retém a última troca causal e dados canônicos essenciais; remove
projeções opcionais antes de histórico antigo. Plano/códigos correspondem ao
snapshot final. Orçamento é estimativa declarada de envelope (caracteres/4),
sem alegar tokenizer exato. Overflow irredutível é explícito e local, não falha
de provider. Native payloads sociais são checados antes de envio. ACIR conserva
atores/semântica; content literal fica nos ConversationTurns, fora de system.

Timeout NVIDIA e timeout do caller possuem códigos separados. Backoff/circuit
respondem a falha real do provider; limite curto de superfície não o penaliza.
Replay estruturado usa realize + validator, sem commit/usage/circuit do Core.
Parentesco usa direção da aresta, não substrings em qualquer relação.

AMORA_SOCIAL_EXPRESSION_PROFILE=amora-v3 ativa o arquivo V3 original, SHA-256
884a77ebce1a9513f35b0ee0b05647bbf9331de6ef667e1e37c451a9cc552a1a.
Default core-v1 e rollback explícito para core-v1. Perfil viaja no snapshot de
contexto, integra realização social e orçamento; não altera primary/modelo.
V3 pode orientar humor adulto espontâneo sem depender de play elevado da
fixture, mas turno, fatos e vínculo familiar continuam restrições do Core.

Staging validado nesta fase é local isolado com provider simulado. Nenhuma
ativação remota em staging/produção ou certificação comportamental Fase 3 é
inferida de testes, build ou presença do arquivo no Docker.
