# Amora Web — Web Query Planner Groq/Qwen (Run 2.5)

Estado: implementação em PR; **CI verde não significa deploy Northflank/Discord certificado**.

## Por que e como

A mensagem literal "Okay entao ou amora pesquisa novidade sobre android na web da godot engine" produzia resultados de música "OKAY" porque a pesquisa originalmente incluía conversa incidental. A correção lexical PR #335 limpou a consulta, mas não formulava uma estratégia semântica.

Agora o Core, **apenas diante de intenção explícita de pesquisa em DM Discord do proprietário verificado**, chama o **mesmo Groq/Qwen 3.8 27B** que já integra o conjunto de modelos Amora. O modelo **não produz a resposta conversacional**, só 1–2 buscas em JSON; não recebe memórias, relações, história completa ou credenciais. Ele pode reformular em inglês para temas técnicos, mas deve preservar as entidades do pedido.

Fluxo:
1. `parse_web_intent` detecta `search`; links e `follow` continuam diretos e **não chamam o planner**.
2. Extrator determinístico produz fallback do assunto (sem preâmbulos).
3. Qwen Groq (`GROQ_API_KEY` preexistente) recebe até 600 caracteres da mensagem de pesquisa e o assunto extraído. Usa `response_format=json_object`, `reasoning_effort=none`, até 230 tokens, timeout hard 3,5s.
4. Validação local exige que as propostas mantenham ao menos duas entidades distintivas (ou uma quando a pesquisa só contém uma); recusa URLs, nomes de hosts internos, strings vazias ou fora do tema.
5. Buscador independente tenta cada motor para a primeira consulta; **só se nenhuma fonte relevante aparecer** usa a segunda. Ranking sempre compara resultados à **intenção original**, não apenas à proposta do modelo.
6. A leitura e a saída continuam do Core, com evidências e URLs de fontes efetivamente lidas. Um modelo não pode criar links citáveis ou navegar rede interna.
7. Se Qwen não responder, exceder tempo, faltar a chave, devolver JSON ruim ou sair do tema: usa a pesquisa determinística da PR #335, **sem Gemini obrigatório e sem interromper a busca**.

**Não usa Chromium no Core**; sites JS ainda usam o Reader externo somente quando HTTP não basta.

## Configuração e custos

- `AMORA_WEB_QUERY_PLANNER_ENABLED=1` (padrão; `0` desliga instantaneamente).
- `AMORA_WEB_QUERY_PLANNER_MODEL=qwen/qwen3.8-27b` (padrão; pode apontar para outro modelo compatível disponível na conta Groq).
- `GROQ_API_KEY` já é utilizada por outros subsistemas; não exige chave nova.
- Planejador adiciona **uma chamada opcional** por pesquisa, limitada a 3,5s. O orçamento total de web em DM fica em 11s (HTTP direto) ou 16s (quando Reader externo é ativado). Não promete latência constante.
- A chamada consome tokens e limites da conta Groq; em indisponibilidade regride ao fallback local. Groq é **provedor de inferência** e Qwen é **modelo**; o nome é distinto do Grok da xAI.

## Privacidade e controle

O Core continua sendo a autoridade: apenas DM Discord do proprietário verificado, escopo PERSONAL e autorização de processamento remoto. Chamadas do planejador não são permitidas para conversas normais, mensagens públicas ou pedidos de abrir URL. Não salva frases pesquisadas como memória. O modelo recebe apenas texto do pedido web; cuidado com segredos incluídos no próprio texto da pesquisa, já que são enviados ao provedor Groq. Resultado modelo é tratado como proposta não confiável e novamente filtrado por regras locais.

## Testes

`python -m pytest tests/test_web_groq_query_planner.py tests/test_web_search_relevance.py tests/test_web_tools_run1.py -q`

O teste com a frase real do Discord deve mostrar consultas sobre Godot/Android, sem resultados sobre músicas gospel ou verbetes de "okay". Deve sobreviver a timeout e 429 de Groq sem bloquear o fluxo.

Requer novo deploy da main no Northflank e teste Discord ao vivo para afirmar sucesso de produção.
