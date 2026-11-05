# Incidente Web/Safari — HTTP 504 após Mistral social primary

**Data:** 2026-11-04
**Escopo:** Web `POST /v1/web/utterance`, configuração de production `nvidia_mistral_large_4`.

## Observação e causa do código

O usuário mostrou uma captura do Safari Lab com `Erro no turno: 504: request timeout`
mesmo com o Core conectado e o microfone desligado. Esse texto literal é
emitido por `core/amora/security.py` quando a aplicação ainda não iniciou
sua resposta após o deadline HTTP de **90s**. O endpoint
`/v1/web/utterance` aguarda síncronamente
`conversation.observe_completed_utterance()`, que aguarda
`cognition.respond()`; portanto a requisição fica aberta durante toda
a chamada de IA.

O Mistral via NVIDIA pode emitir centenas de eventos de reasoning antes
de qualquer texto visível e o adapter de produção foi configurado com
até **95s** de teto total, além de **55s** de inatividade. Sem budget por
turno Web, esse limite de Mistral podia ultrapassar o limite de HTTP,
impedindo o fallback e transformando uma resposta lenta num erro 504.

A captura **não prova que todas as falhas 504 são esse caso**: infra/proxy
externo, rede e DB também têm limites próprios. A identificação da
mensagem exata com o `SecurityMiddleware` sustenta a hipótese principal.

## Correção

Não elevar o deadline global da segurança. Na rota de texto Web, fornecer
orçamento local de provider por meio do metadata que o Core já suporta:

```text
Web HTTP SecurityMiddleware:             90 s (não alterado)
Web Mistral/Groq: timeout por tentativa: 65 s
Web soma de tentativas de providers:      76 s
Reserva para overhead Core/DB/HTTP:      14 s (não é SLA)
```

Se o Mistral exceder 65s, `structured_runtime` cancela a tentativa,
registra falha e tenta Groq/Qwen com o budget restante, ainda aplicando
`RealizationValidator` antes de encaminhar fala. Fallback para Gemini/
OpenRouter só ocorre se elegível e se ainda houver orçamento. O
`reasoning_effort`, modelo preferido e limites da certificação não
mudam. Isso é proteção de **transporte síncrono Web**, não troca de
modelo nem limitação genérica da personalidade.

O sistema pode ainda expirar se processamento não-provider, storage,
infra ou proxy consumirem tempo demais; isso exige observabilidade
específica e eventual reestruturação em job assíncrono com
`turn_id` + polling/stream, preferível a aumentar limites indefinidamente.

## Testes

- Metadata Web chega ao `CognitiveRequest` mesmo em test mode;
- Primeiro ranking social continua Mistral;
- Mistral simulado lento é cancelado no deadline de tentativa;
- Groq simulado responde como fallback dentro do limite;
- Web devolve HTTP 200 e a fila de voz contém fala válida;
- Nenhum guardrail é removido e o middleware de segurança continua a 90s.

## Evidência operacional recomendada

Depois do deploy do novo `main`, com cognição remota e modo teste ligados,
fazer duas ou três falas de texto curtas no Safari. Registrar **somente**
HTTP status, latência e provider efetivo pelo journal/telemetria autorizada;
não registrar prompt, bearer, confidências nem texto de reasoning.
Mistral pode continuar levando dezenas de segundos. Testes CI com provider
simulado demonstram failover correto, não garantem latência real da
NVIDIA ou proxy do Northflank.


## Etapa 2: transporte Web SSE ativo (2026-11-04)

A limitação dos budgets 65/76 do POST síncrono mostrou-se insuficiente
em uso real: outro 504 apareceu inclusive após uma conta simples.
O servidor continuava guardando toda a resposta até o fim da chamada,
com o timeout HTTP global de 90s independente de atividade.

A rota nova `POST /v1/web/utterance/stream` mantém o contrato anterior
`POST /v1/web/utterance` para compatibilidade, mas passa a responder em
`text/event-stream` via fetch streaming no Safari:

- `accepted` imediatamente após autorização;
- `progress` com categoria fixa `reasoning` ou `writing`, **apenas**
  quando o provider transmite eventos significativos;
- comentário `: heartbeat` a cada até 8s sem sinal novo, exclusivamente
  para manter o HTTP/proxy vivo (não comprova atividade do provider);
- `completed` com o JSON legado **após** validação do `RealizationFrame`
  e commit/fila canônicos;
- `failed` com categoria estática sem divulgar prompts, chunks de
  reasoning, fala recusada ou exceções privadas.

A partir dessa mudança, o POST normal mantém 90s, mas a rota SSE tem
**270s de teto operacional de segurança** no middleware. O adapter
produtivo NVIDIA continua com **55s de inatividade significativa** e
agora possui **185s de teto de segurança** para seu fluxo próprio.
A nova rota define budgets Core por provider de 190s e total de 225s
para fallback seguro dentro do teto de 270s. Outros endpoints não têm
esses limites modificados.

O navegador desativa envio concorrente e não retransmite um POST SSE
automaticamente após desconexão. O processo do Core é executado numa
`asyncio.Task` fortemente referenciada, permitindo concluir a fala
mesmo se a conexão do Safari for encerrada, até os limites operacionais
do servidor. Voz e bolha definitiva só aparecem depois da validação;
**stream de raciocínio NÃO é texto para falar**. O caminho de STT/microfone
ainda usa seu endpoint síncrono e não faz parte desta correção.

Se a infraestrutura da hospedagem impuser limite absoluto inferior,
ou se persistência/DB excederem 270s, essa melhoria não garante
ausência universal de timeouts. Mais adiante, uma fila durável de turnos,
com idempotência e recuperação de sessão, pode substituir as tasks
efêmeras. Não declarar SLA ou vitória em ambiente real sem novos logs
evidenciais.
