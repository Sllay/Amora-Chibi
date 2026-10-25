# Security pass pré-deploy — 2026-10-23

Política Web atual: ADR-0018 substitui TTL 12h/sessionStorage deste snapshot
por credencial Web v2 durável, sempre limitada, e localStorage. Avatar conserva
v1/12h. Não muda escopos administrativos nem os outros gates de segurança.

Atualização 2026-10-24: o bloqueio da imagem foi resolvido na etapa de runtime
com base oficial Alpine validada; ver [decisão e evidências](RUNTIME_BASE_DECISION.md)
e ADR-0017. O resultado abaixo descreve o snapshot Debian do commit eb03eb8.
PASS do runtime não aprova deploy: staging continua um gate separado.

**Resultado: NÃO APROVADO PARA DEPLOY.** Controles de aplicação e container foram
implementados e testados; restam 44 ocorrências HIGH do Debian, relativas a oito
CVEs, sem FixedVersion no banco consultado. Não houve exceção/VEX/ignore de CVE.
O gate de imagem novo deve falhar enquanto esses achados permanecerem.

## Dependências e imagem

- Core: pip-audit 2.10.1 auditou os 23 pacotes de `deploy/python.lock`; zero
  vulnerabilidades conhecidas, zero pacotes pulados. O lock existente não foi
  atualizado sem motivo; Pydantic beta continua pela decisão de compatibilidade
  já documentada. As versões são fixas, mas o lock não usa hashes de artifacts.
- Voice Worker: resolução completa fixada em `deploy/voice-worker.lock` (17
  pacotes, incluindo sherpa-onnx/core 1.13.8), auditada sem achados ou skips.
  Template Modal passa a consumir esse lock. O bundle nativo/ONNX real e o
  runtime injetado pela plataforma Modal ainda exigem análise em staging.
- Trivy 0.76.0 pinado por digest auditou separadamente base, imagem anterior e
  runtime final. Base: Python 3.12.15/OpenSSL 3.5.7, Debian 13.7, digest
  `a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704`.
- Removidos do runtime pip/setuptools/wheel e vendors vulneráveis. Builder usa
  pip 26.2.2, setuptools 83.0.0 e wheel 0.46.2, sem levá-los ao estágio final.
  Antes: 3 HIGH, 6 MEDIUM, 1 LOW de pacotes Python; depois: zero.
- Sistema operacional permanece com 44 HIGH, 58 MEDIUM, 61 LOW e 1 UNKNOWN.
  HIGH: CVE-2026-76642, 78408, 78409, 78410, 54369, 16742, 9538 e
  CVE-2025-69720. Sete estão affected; CVE-2026-9538 está fix_deferred.
  Mitigações de execução não provam que esses CVEs sejam inexplotáveis.
- Evidências: `python-lock-audit.json`, `voice-lock-audit.json`, `image-audit.json`.
  Scans descrevem as bases consultadas nesse instante; não garantem ausência de
  vulnerabilidades desconhecidas ou cobertura integral de código nativo/stdlib.

## CI e Node

Node dos testes fixado em **22.23.4**, com verificação explícita do piso. Binário
oficial Linux x64 testado localmente após conferir SHA-256
`1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce`
contra `https://nodejs.org/dist/v22.23.4/SHASUMS256.txt`.

Actions checkout v5, setup-node v5, setup-python v6, setup-java v5,
upload-artifact v6 e Gradle v5 conferidas nos metadados oficiais como Node 24 e
pinadas por commit (`action-pins.json`). A patch do Node embutido no runner é
responsabilidade do GitHub; scripts da aplicação usam a versão explicitamente
instalada. Avatar não possui dependências npm externas.

`security-predeploy.yml` audita ambos os locks e bloqueia HIGH/CRITICAL da imagem,
incluindo unfixed. Relatórios são artifacts mesmo quando o gate falha. Não há
continue-on-error nem lista de exclusões. CI remota não foi observada nesta sessão.

## Autenticação, pairing, operações e WebSocket

Inventário real das **69 operações HTTP** em `ENDPOINTS.md`. Gate ASGI protege
qualquer rota privada mesmo se uma futura rota esquecer Depends. Documentação
OpenAPI/Swagger/Redoc não é publicada. Operações/admin, estado bruto, memória,
world, journal, replay e tools exigem admin.

Cloud pairing:

- Código ASCII de seis dígitos; validade 5 min, máximo cinco tentativas e consumo
  único sob lock entre threads/processos da mesma máquina.
- Limite adicional: cinco requisições/minuto por peer e dez/minuto global,
  inclusive entradas inválidas. Não usa X-Forwarded-For arbitrário como identidade.
- Retorna token web/avatar assinado e expirável em 12h, nunca o admin. Web só
  acessa superfícies conversacionais WEB_SAFARI; Avatar só /v1/avatar/*.
- Assinatura e comparação resistentes a diferenças de timing; Unicode inválido
  no header não produz 500. Em cloud, token administrativo mínimo de 32 bytes;
  gerar aleatoriamente. Token de serviço de voz também mínimo 32 bytes em cloud
  e distinto do admin. Não colocar valores em chat, Git ou imagem.
- Rotação do admin invalida todos os tokens de browser. Se uma instalação antiga
  já entregou admin ao navegador, rotacioná-lo antes de torná-la pública; limpar
  o browser sozinho não revoga cópias antigas. Não há revogação individual ainda. Android debug local conserva o contrato anterior; pareamento cloud não concede automaticamente DEVICE_A15/admin. Living Console exigirá provisionamento administrativo explícito ou papel próprio futuro.

`/v1/operations/state` permanece admin e projeta apenas estados/códigos. Console
TTY está desativado em cloud; nenhum console remoto/MCP novo foi exposto.

WebSocket `/v1/events/stream`: admin no header, nunca query/cookie; Origin exato
quando presente. Browser sempre envia Origin; clientes nativos autenticados
podem omiti-lo. Tokens web/avatar não acessam o stream. AUTH/SECRET são filtrados;
entrada de comandos é recusada com 1008. Máximo 2 conexões por peer/32 globais,
30 min de vida, mensagens de até 16 KiB no Uvicorn, ping/pong timeout 20s.
Disconnect/cancel libera subscriptions inclusive sob cancelamento AnyIO.

## CORS, proxies e fronteira de rede

`AMORA_PUBLIC_ORIGINS` é obrigatório em cloud (origens HTTPS exatas). Hosts não
confiáveis e Origin divergente/null são recusados. Sem wildcard ou middleware
CORS permissivo; não são emitidos headers Access-Control-Allow-Origin/Credentials.
Uvicorn ignora forwarded headers por padrão. `AMORA_TRUSTED_PROXY_IPS` aceita só
IPs/CIDRs explícitos, rejeita wildcard, /0 e endereços inválidos. O ingress precisa
preservar Host/Origin, terminar TLS e sobrescrever headers de proxy fornecidos
pelo cliente. A configuração real de Koyeb/Modal ainda não foi validada.

Providers usam hosts fixos; Cloudflare account ID não pode injetar segmentos de
URL. Voice Worker/libSQL exigem origem HTTPS pública, porta 443, sem userinfo,
query/fragment, percent escapes, backslash, hostname local ou IP privado/reservado.
Redirects estão explicitamente desabilitados. Não há parâmetro URL controlado
por cliente nas rotas. Mudanças de origin sugeridas pelo storage são recusadas.

**Limite explícito:** no ambiente com proxy, DNS é resolvido pelo proxy. Validação
lexical não prova proteção contra DNS rebinding. Egress do host/proxy deve negar
RFC1918, loopback, link-local, metadata e destinos fora da allowlist, inclusive
após resolver DNS. Esse controle e os destinos reais precisam de prova em staging;
não foi criado um transporte que ignore o proxy/credenciais injetadas.

## Limites de custo, upload e tempo

Gate antes de parsing/handlers: JSON geral 256 KiB; pairing/worker 16 KiB;
áudio e assets 12 MiB. Limite é aplicado também durante leitura chunked; gzip
não aceito. Framing ambíguo, headers duplicados críticos, Content-Length inválido,
headers >16 KiB, query >8 KiB, target >4 KiB ou NUL/controles são recusados.
Erros de validação não devolvem o input nem secrets em detalhes.

Body timeout 10s; request budget 90s; keep-alive 5s. Providers têm timeouts de
conexão/leitura próprios; voz remota 20s e storage 15s. APIs caras: 20/min por
peer, 60/min globais e quatro requests simultâneos, incluindo o período de upload. Até 32 bodies totais simultâneos limitam alocação de buffers. Render nativo tem slot próprio
que continua ocupado até o compute terminar; timeout HTTP não permite enfileirar
renders indefinidamente. Cancelar HTTP não promete preempção de biblioteca nativa.

Requests gerais 600/min, falhas de autenticação 20/min por peer; probes 60/min
por peer/120 globais. Mapas são bounded e não expulsam entradas ativas para
permitir bypass. Limites são conservadores, em memória e por processo. Uma única
réplica continua obrigatória; CDN/ingress/WAF deve impor limites distribuídos e
conter DoS antes da aplicação. Ajustes precisam de medição com tráfego Safari.

## Web, tokens e logs

CSP: scripts self, sem inline/eval; object/base/frame bloqueados; connect self;
images/media blob/data conforme necessidade. Inline CSS permanece permitido pelo
Avatar existente, não inline JS. X-Content-Type-Options nosniff, Referrer-Policy
no-referrer, X-Frame-Options DENY, Permissions-Policy restringindo sensores e
liberando somente microfone self; HSTS em cloud. APIs por padrão no-store; assets Avatar usam cache privado de 60s e perfis de voz no-cache.

Não há cookie de autenticação ou token em URL. Bearer fica em sessionStorage;
versões Web/Avatar descartam a cópia legada de localStorage. O cache de shell Avatar foi versionado novamente; o service worker já exclui /v1 e POSTs do cache. Preferências não
secretas continuam persistentes. sessionStorage ainda é acessível a JavaScript;
CSP/escape/revisão de XSS continuam necessários e não são substituídos pelo TTL.
Templates Avatar escapam valores antes de innerHTML; Web usa textContent.

Formatters JSON/console redigem valores observados pelo SecretStore, credenciais
Bearer e query keys. Testado com extras estruturados e secrets contendo aspas.
Bodies, Authorization e cookies não são acrescentados a logs de requests; erros
operacionais usam códigos/IDs. Isso não autoriza logar payloads privados contando
com redaction como proteção universal. Health/ready cloud públicos só expõem
status; não retornam versão, relações, memória, providers ou paths.

## Container e evidência funcional

Imagem multi-stage, UID/GID 10001, sem ferramentas de build no runtime. Smoke
executado com `--read-only --cap-drop=ALL --security-opt=no-new-privileges`, /tmp
em tmpfs noexec/nosuid e único volume gravável em AMORA_HOME. Validou endpoints,
texto autenticado, fila persistente, restart e SIGTERM exit 0. Configurar esses
flags no host: Dockerfile sozinho não torna filesystem read-only. Para SQL remoto
com assets no banco, AMORA_HOME pode ser tmpfs uid 10001 com espaço adequado a
logs/cache/uploads. Modelos/libs do worker devem ser read-only e verificados.

347 testes Python passaram (incluindo 43 novos adversariais); cinco testes
Avatar passaram em Node 22.23.4; pip check e checks de sintaxe passaram. Testes
cobrem inventário privado, roles/expiração, pairing/rate, WebSocket origin/auth,
CORS/headers, uploads/chunked, URL SSRF, framing, malformed/deep JSON, targets,
secrets/redaction, timeout e render ocupado. Nenhum teste físico Safari foi alegado.

## Antes de publicar

1. Resolver ou analisar formalmente os oito CVEs HIGH do Debian e repetir o gate;
   não aprovar a imagem por ausência de CVEs Python.
2. Provisionar origens/TLS/peers de proxy/egress e provar que DNS não alcança redes
   privadas/metadata. Aplicar limites de borda; manter uma réplica.
3. Usar secrets novos/aleatórios; rotacionar admin anteriormente distribuído ao
   browser, se aplicável. Reparear clientes. Nenhum valor deve ser enviado em chat.
4. Repetir smoke e testes de latência/limites em staging Koyeb/Turso/Modal e Safari;
   verificar bundle de voz, SDK/runtime gerenciado e retenção de logs/backups.

Não houve deploy, alteração de personalidade, memória/relações ou parâmetros vocais.

A imagem final auditada e o timestamp estão em `image-audit.json`. O comando
Trivy com `--severity HIGH,CRITICAL --exit-code 1` foi executado localmente e
retornou **1**, confirmando o bloqueio. O bloqueio é do scan de imagem; os testes funcionais passaram.
