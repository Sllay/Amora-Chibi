# Amora — fontes web clicáveis e prints por Discord sem Chromium local

**Motivação (20/11/2026):** a Amora relatava não possuir links apesar de executar
pesquisas. O modelo conversacional pode falhar ou declarar indisponibilidade
quando o Core ainda possui URLs válidas de resultados. O envio das fontes não
deve depender da fala do modelo.

## Links garantidos pelo transporte, quando a busca realmente encontra URLs

- O Core compõe um apêndice separado, após a fala, com **Fontes consultadas**
  (páginas efetivamente abertas) e/ou **Resultados da busca (nem todos lidos)**
  (URLs de listagens, não evidências de conteúdo lido).
- Os URLs só vêm do WebNavigator/verificação do Core, nunca de links fabricados
  pelo modelo de linguagem. `CognitiveEngine` continua dono da busca e
  `MessagingService` dono da mensagem de saída.
- Quando o modelo da conversa cai em fallback, as referências mais recentes
  da sessão privada ainda são visíveis pelo Core, em vez de serem perdidas.
  Para cada nova busca a lista anterior é limpa **antes** de pesquisar,
  evitando citações de resultados desatualizados.
- A numeração original é mantida para `abre o link 2`. Uma página aberta pode
  também ser listada como resultado, sem esconder que os links restantes não
  foram lidos.
- **Se não existirem resultados de verdade, nenhum URL é inventado.**

## Prints solicitados explicitamente

Exemplos de uso:
```text
Amora, pesquisa novidades do Godot para Android e manda um print da página.
Amora, abre https://godotengine.org e me envia um print.
Amora, tira print do link 2.
```

1. Somente uma mensagem do **proprietário verificado em DM Discord** e com
   intenção explícita de screenshot recebe
   `web_capture_source` + `web_capture_owner_verified=True` pelo Core.
   Outros usuários, grupos, prompts de página e a fala do modelo **não**
   autorizam capturas.
2. O worker Discord (Node 22) usa HTTPS para pedir **uma captura remota
   Microlink**, com screenshot em JPEG e qualidade limitada. Não executa
   Chromium e não aumenta a RAM do Core.
3. A API retorna URL de screenshot hospedada no CDN oficial; o worker baixa
   apenas de `cdn.microlink.io` por HTTPS, com 9 s de limite e até 2,5 MB,
   verifica o tipo e os bytes da imagem.
4. O worker envia a captura via **multipart/form-data** para o mesmo endpoint
   de mensagem Discord, com a imagem como anexo, e usa o nonce idempotente
   existente. O texto sempre inclui as fontes.
5. Se o serviço estiver indisponível, quota esgotada, screenshot ruim ou
   arquivo grande, a mensagem de links é enviada mesmo assim, com um aviso
   breve. Falhar na captura **não pode silenciar o diálogo**.

## Configuração

Discord Worker:
- `AMORA_WEB_SCREENSHOTS_ENABLED=1` — default; `0` desliga prints imediatamente.
- `AMORA_WEB_SCREENSHOT_DAILY_LIMIT=4` — captura novas até 4 vezes por dia
  UTC por processo Discord Worker (reserva antes do acesso). O contador é em
  memória; reinícios reiniciam o limite local. Não representa quota global da
  Microlink e não substitui limites do provedor.
- `AMORA_WEB_SCREENSHOT_MICROLINK_API_KEY` — **opcional**. Se definido, usa
  `https://pro.microlink.io` com `x-api-key`; caso contrário, usa
  `https://api.microlink.io`, endpoint livre enquanto disponível.
  Não coloque segredos no repositório.

O serviço Microlink informa oferta gratuita de 25 solicitações por dia com chave
gratuita; a disponibilidade keyless e quotas podem mudar. Caso o endpoint
não aceite pedidos sem chave, fornecer chave gratuita diretamente como segredo
no Northflank. O worker não instala pacote adicional nem navegador residente.

## Privacidade e limitações

- Nenhuma captura automática em toda pesquisa: somente sob solicitação clara.
- O URL de destino é enviado à Microlink; não são enviados cookies do Discord,
  memórias, histórico pessoal, credenciais ou prompt de Amora.
- Bloqueia IP literal, localhost, hosts internos, URL com userinfo, parâmetros
  convencionais de token e portas não web. Valores secretos disfarçados no
  *path* ou em query com nome incomum não são detectáveis por regras simples;
  **não use prints externos em links privados ou autenticados**.
- Sem automação de login, bypass de CAPTCHA ou captura garantida de todas as
  páginas. CDN e renderização são de um terceiro e podem recusar sites.
- O worker não busca diretamente o site; a Microlink faz essa renderização.
  A política de rede do provedor precisa bloquear recursos privados e de
  metadata na sua própria infraestrutura.
- A imagem anexada é uma observação visual do site, **não atestado de
  veracidade das informações**. Uma falha nunca justifica URL inventado.
- O recurso está validado em testes de contrato, não na operação ao vivo.
  Requer deploy de Core + worker Discord e teste manual no DM.

## Certificação

```sh
python -m pytest tests/test_web_discord_link_delivery.py tests/test_web_tools_run1.py -q
node --test tests/discord_web_screenshot.test.mjs
```

### Referências oficiais

- [Microlink API / screenshots](https://microlink.io/api)
- [Microlink screenshot guide](https://github.com/microlinkhq/www/blob/master/src/content/docs/guides/screenshot/index.md)
- [Discord Create Message / multipart attachments](https://github.com/discord/discord-api-docs/blob/main/developers/resources/channel.mdx)
