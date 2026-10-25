# ADR-0016 — Fronteira de segurança para Core/Web público

Status: ACCEPTED. Data: 2026-10-23. Refina ADR-0015.

Atualização 2026-10-25: ADR-0018 substitui somente a política Web de TTL 12h e
sessionStorage por credencial Web v2 durável e localStorage limitado. Escopos,
segregação administrativa e demais gates abaixo permanecem. Avatar mantém v1.

## Contexto e evidência

O fluxo local entregava o mesmo bearer administrativo ao browser após pareamento.
Isso deixa de ser aceitável quando o Core é público. Faltavam limites globais de
body/rate, origem WebSocket, trusted proxy explícito e headers Web. A auditoria
pré-deploy encontrou também pacotes vulneráveis no tooling da imagem.

## Alternativas e decisão

Evitar colocar cookies/sessões ambientais ou autenticação de negócio no Safari.
Manter Bearer explícito e um gate ASGI anterior aos handlers. No modo cloud,
pareamento emite credencial assinada, com nonce, escopo web/avatar e validade
12h. O token administrativo não é distribuído por pareamento cloud. Rotacionar
admin invalida todas as credenciais derivadas; revogação individual/distribuída
é futura e não é alegada nesta etapa. Desenvolvimento local conserva o contrato
anterior e não deve ser publicado como produção.

Origins/hosts são explícitos, CORS continua fechado, WebSocket administrativo
não aceita token em query/cookie, e forwarded headers não são confiáveis por
padrão. Proxies exigem IP/CIDR explícito, sem wildcard ou rede /0. Limites são
locais à única réplica; proteção distribuída de borda é gate de implantação.

Browser usa sessionStorage, não persistência de bearer em localStorage. Não há
cookie autenticador. CSP permite scripts somente da própria origem; inline CSS
permanece necessário ao Avatar existente, sem permitir inline script/eval.

## Consequências e critérios de revisão

TTL/escopos são controles de acesso, não mudanças de identidade/persona. Cliente
pode precisar repetir pareamento após expiração/rotação. Origens de tunnels de
desenvolvimento também precisam ser declaradas. A15, memória e voz congelada não
mudam. Worker recebe apenas a credencial de serviço já separada.

Scan não é certificação completa. Imagem runtime sem tooling Python vulnerável,
mas os HIGH do Debian ainda impedem aprovação de deploy. Não adicionar exceções
silenciosas ou ignorar achados sem análise/revisão. Revisitar com patches de base,
necessidade de revogação individual, várias réplicas ou política explícita de BK.
