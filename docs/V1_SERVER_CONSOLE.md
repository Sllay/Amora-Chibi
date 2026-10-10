# V1 Server Console

O console do Core é uma superfície operacional local. Ele não é a memória da Amora e não é a fonte canônica de identidade.

## Objetivos

- não poluir o terminal com polling HTTP repetitivo;
- atualizar uma única interface no lugar de imprimir uma linha nova a cada request;
- mostrar atividade de alto nível (`TRANSCRIBINDO`, `PENSANDO`, `RESPONDENDO`, `FALANDO`, `IDLE`);
- mostrar saúde de módulos, providers e plugins;
- destacar erros reais de forma curta e deduplicada;
- manter diagnóstico completo no log estruturado `amora.jsonl`;
- permitir que plugins futuros apareçam automaticamente na lista de saúde.

## Estados de módulo

- `ON`: disponível/saudável;
- `OFF`: indisponível ou desconectado;
- `WAIT`: temporariamente indisponível, por exemplo cooldown/rate limit;
- `DEGRADED`: existe e continua isolado, mas uma execução recente falhou.

A saúde runtime de plugin é separada do manifest. O manifest declara a condição inicial; `PluginRegistry` mantém a condição operacional corrente. Falha de plugin não derruba o Core.

Exemplo futuro: um plugin `games.minecraft` pode iniciar `OFF`, mudar para `ON` ao conectar a um servidor e voltar para `DEGRADED/OFF` se a conexão falhar. O console não precisa conhecer Minecraft especificamente: ele enumera o registry.

## Logs

O terminal não é o arquivo de auditoria. INFO detalhado, métricas, tracebacks e eventos repetitivos ficam no JSONL rotativo. O terminal recebe apenas a visão resumida e erros recentes.

`uvicorn.access` e INFO de `httpx` não devem dominar a superfície humana. Polling de `/v1/conversation/next` e ACKs continuam funcionando, mas não geram uma nova linha visível a cada ciclo.

## Cognição exibida

O console pode evoluir para mostrar estado cognitivo local observável: intenção ativa, estágio do turno, provider escolhido, `DialogueAct`, ação/tool em execução e resumos do Cognitive Journal.

Ele **não** deve exibir chain-of-thought privada do provider. O objetivo é observabilidade da arquitetura da Amora, não despejo de raciocínio interno oculto.

## Voz e quota

TTS remoto deve falhar fechado para fallback local. Erros 429 do Gemini entram em cooldown com `Retry-After`; durante o cooldown o módulo aparece como `WAIT` e novas falas usam Android TTS sem martelar a API remota.

A rota de áudio deve validar/abrir o stream remoto antes de confirmar HTTP 200. Assim uma quota esgotada vira 503 normal para o cliente em vez de um stream 200 quebrado.
