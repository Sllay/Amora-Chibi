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
