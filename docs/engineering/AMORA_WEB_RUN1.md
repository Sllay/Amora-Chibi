# Amora Web — Run 1 (leitura direta, sem provedor obrigatório)

Status: código integrado por PR #332; **não implica deploy ativo nem Discord ao vivo certificado**.

## O que roda
- **web.open:** URL explícita enviada pelo proprietário no DM Discord autorizado. Faz GET do endereço público indicado diretamente, sem consultar índice de buscas nem lista de sites aprovados.
- **web.extract:** texto principal de HTML, além de texto simples, JSON e XML de tamanho limitado; extrai até 12 hyperlinks numerados para o próximo turno.
- **web.follow:** depois de uma consulta, “abre o link 2” usa somente referências capturadas naquela conversa nos últimos 15 minutos; todas as URLs voltam a passar pela mesma validação de rede.
- **web.search:** SearXNG configurado opcionalmente (API JSON), depois Bing RSS e DuckDuckGo HTML como mecanismos sem chave. Cada mecanismo pode negar acesso, mudar o formato ou exigir CAPTCHA; nunca alegar resultados que não obteve. Busca lê até duas páginas encontradas em paralelo; o modelo distingue páginas efetivamente abertas de meros resultados.

## Portas de execução
O Core executa apenas quando todas as condições são verdadeiras: mensagem Discord, proprietário verificado `PERSON_BK`, DM privado, permissão explícita de processamento remoto, intenção web explícita reconhecida, resposta social autorizada, modo cognitivo `reply`, sem `test_mode`. Superfícies públicas e outros usuários **não** disparam pesquisa. Navegação ocorre por até nove segundos extras no pipeline de cognição; não dispara em chat comum.

Sinal de ambiente opcional `AMORA_WEB_ENABLED=0` desativa a execução. `AMORA_WEB_SEARCH_ENDPOINT=https://searx.exemplo.org` prioriza um servidor SearXNG com JSON habilitado (não pressupõe instância pública funcional). Nenhuma nova chave ou dependência nativa é necessária. A própria hospedagem precisa ter egress público HTTPS/HTTP disponível; caso contrário, os códigos de erro são retornados ao modelo.

## Rede e limites
- Métodos somente GET, esquemas `http` e `https`; sem formulários, POST, download executável, Javascript, login, cookies, cabeçalhos de autenticação, proxies de ambiente ou comandos de shell.
- DNS resolvido para **todos** os registros e recusado se houver qualquer IP não global, endereço reservado, loopback, link-local ou interno, incluindo IPv4 mapeado em IPv6. TCP conecta **somente** no IP aprovado; TLS/SNI confere hostname original. Cada redirecionamento tem a mesma proteção. Não existe allowlist de domínios públicos ou filtro por controvérsia do conteúdo.
- Portas públicas típicas de web: 80, 443, 8080, 8443. Até 3 redirecionamentos, 420 KB de corpo, socket com timeout de 4 s, até 2 páginas de evidência por busca, 2.400 caracteres por fonte. URLs sem credenciais embutidas; sessões de links efêmeras e isoladas, até 32 sessões.
- CAPTCHA, HTTP 401/403/429, conteúdo binário, páginas inteiramente renderizadas por JS e WAF podem ser inacessíveis nesta Run 1. Browser Playwright e documento PDF pertencem à Run 2.

## Fonte, prompt injection e memória
O Core passa `web_research` no `TurnFrame` como **dados não confiáveis** e não insere conteúdo web no mundo, identidade ou memória automaticamente. O modelo deve descrever o que leu e preservar incertezas e divergências. A própria lógica do Core acrescenta URLs de fontes efetivamente abertas ao texto do Discord, independente de alucinação textual do modelo. Falha de fetch jamais vira confirmação.

**Nenhuma validação live foi feita** contra Discord ou os motores gratuitos; toda disponibilidade real depende do endereço e da infraestrutura. Para a bateria de testes futura: link com conteúdo real, redirecionamento para IP privado, site com JS/CAPTCHA, busca com três fontes e follow “abre o link 2”; conferir resultado, fontes, privacidade, timeout e custo.

## Testes
`python -m pytest tests/test_web_tools_run1.py -q`. A matriz `Core tests`, segurança e build/contratos cloud precisam estar verdes antes de merge.
