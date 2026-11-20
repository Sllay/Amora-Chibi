# Amora Web — baixo consumo, JS sem Chromium local

## Escolha operacional (20/11/2026)

O Core roda com orçamento pequeno (0,2 vCPU e 512 MB). **Não implantar o
serviço Chromium/Playwright da Run 2 nessa infraestrutura**. Ele permanece no
repositório apenas como alternativa opt-in para outra máquina adequada; não
é requisito, não inicia automaticamente e não deve ser configurado por padrão.

**Pipeline padrão da Amora:**

1. `web.open` / `web.follow`: HTTP direto pelo Core, IP de destino validado
   e fixado no socket, sem domínio público pré-aprovado.
2. Extração local simples de HTML/texto/JSON. Se houver conteúdo bom, encerra.
3. HTML vazio, app JS ou HTTP 403: uma requisição **HTTP(S) outbound** para
   o Reader hospedado em `https://r.jina.ai/`. O **JavaScript roda na
   infraestrutura do Jina**, não no Core. O Core recebe apenas Markdown.
4. Se o serviço externo falhar/limitar/bloquear, dizer que não conseguiu
   ler o conteúdo. **Não inventar fontes** nem transformar snippet em página lida.
5. Configuração opcional de uma instância privada do antigo Chromium
   continua como *última* alternativa, mas é desaconselhada na hospedagem de
   512 MB. O Core **não contém nem instala o Playwright**.

Busca pública continua usando SearXNG quando configurado, mais Bing RSS e
DuckDuckGo HTML; não depende do Reader para encontrar URLs.

## Configuração

- `AMORA_WEB_REMOTE_READER=1` — padrão, renderização externa sob demanda,
  sem criar API key. Para desativar qualquer envio ao serviço terceiro,
  `AMORA_WEB_REMOTE_READER=0`.
- `AMORA_WEB_BROWSER_URL` — deixe **não configurado** no servidor pequeno.
- `AMORA_WEB_BROWSER_TOKEN` — não é necessário para este método.
- `AMORA_WEB_ENABLED=1` — mantém a autorização existente: pedido explícito
  somente em DM Discord do proprietário verificado, com processamento remoto
  permitido. Não ativa navegação para mensagens comuns.

Sem novos pacotes ou binários no container Core. Nenhum serviço adicional de
browser precisa ficar permanentemente online. O Reader tem limite anunciado
de 20 chamadas/minuto sem chave (sujeito a revisão, bloqueios e rate limiting),
e pode usar cache ou devolver conteúdo desatualizado.

## Confiança e privacidade

- Apenas o endereço público explicitamente consultado vai ao provedor externo.
  Nunca enviar memórias, prompts, Discord IDs, credenciais ou cookies.
- URLs com credenciais em `userinfo` ou parâmetros típicos `token`,
  `access_token`, `api_key`, `sig`, `password`, `session` etc. **não**
  são encaminhadas. Evite consultar links privados ou secretos com o Reader,
  inclusive quando as credenciais estiverem embutidas no caminho ou em outros
  campos não detectáveis.
- Texto remoto é dado não confiável, incluído como `web_research` efêmero
  e com marcador `retrieval=third_party_jina_reader`. O Core controla
  o URL citado a partir do pedido original; nunca aceita uma URL de citação
  inventada pelo texto de resposta ou pelo cabeçalho da API externa.
- O Jina tem política própria de acesso, capacidade e rate limit: **não há
  promessa de abrir todos os sites**. Sua camada de filtros, caching e
  proteções antibot pode impedir acesso. A leitura HTTP direta continua sendo
  a opção não terceirizada em qualquer domínio público.
- A resposta deve preservar incerteza e não afirmar leitura quando houver
  HTTP 403, 429, desafio anti-bot ou resposta vazia.
- HTTP direto da Run 1 mantém cerca de 9 s de limite de web; quando o Reader
  está disponível, o orçamento total de pesquisa passa a 15 s. Exceder
  esse orçamento não autoriza trabalho em segundo plano ou resposta falsa.

## Validação

`python -m pytest tests/test_web_tools_run1.py tests/test_web_browser_run2.py tests/test_web_remote_reader_low_memory.py -q`

Não foi validada operação ao vivo no Northflank ou Discord, nem a disponibilidade
permanente do endpoint externo. Só considerar produção testada após enviar URL
JS real pelo DM, conferir fonte, tempo e falhas, testar URL com token, 429,
redirecionamento e conteúdo estático normal.

Fontes oficiais:
- https://github.com/jina-ai/reader
- https://jina.ai/reader/
- https://lightpanda.io/docs/core-concepts/what-is-lightpanda
- https://developers.cloudflare.com/browser-run/pricing/
