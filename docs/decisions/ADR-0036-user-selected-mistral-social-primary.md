# ADR-0036 — Mistral Large 4 como realizador social principal (decisão humana)

- Status: **Accepted — user-directed promotion**
- Date: 2026-11-04
- Scope: CH3, somente `TaskClass.SOCIAL_DIALOGUE`
- Supersedes: a proibição de promoção contida em ADR-0034 **apenas após esta decisão explícita**; mantém o candidato e os caminhos de certificação anteriores intactos

## Contexto e decisão

Após execução humana e revisão qualitativa das baterias `SOCIAL_EXPRESSIVE4_V1`
e `SOCIAL_NUANCE4_V1`, o responsável pelo projeto decidiu **usar o
Mistral Large 4 via NVIDIA como provider social principal**, mesmo
aceitando que a latência por turno observada é significativamente maior
do que a do Groq/Qwen. A escolha não decorre de um score automático:
as notas mecânicas de 100 pontos não medem personalidade ou nuance.

O Mistral é promovido **somente para realização de fala social**.
Identidade, memória, relações, expressão, `DialoguePlan`, privacidade,
autoridade de ferramentas, validação antes da fala e estados canônicos
continuam no Core. A promoção não envolve raciocínio técnico ou
permissões. **Não** afirma que a bateria original completa de onze
casos foi executada ou que o endpoint hospedado garante JSON Schema
estrito.

## Implementação e fallback

1. O adapter de certificação `NvidiaMistralLarge4Provider` permanece
   `certification_only`, com nome próprio, `priority=-100` e sem
   registro de produção automático.
2. O adapter separado `NvidiaMistralLarge4ProductionProvider` é
   registrado ao iniciar o Core **quando `NVIDIA_API_KEY` existe**.
   Seu nome no runtime é `nvidia_mistral_large_4`.
3. A seleção explícita de primary tem preferência **apenas no ranking
   social** e **somente após** portas normais de privacidade, quota,
   disponibilidade, circuit breaker e janela de contexto.
4. O Groq/Qwen continua registrado como fallback. Se a chave NVIDIA
   estiver ausente, a Amora continua no provider anterior sem falhar o
   boot. Gemini e outros providers permanecem como antes.
5. Operador pode voltar à escolha anterior com
   `AMORA_SOCIAL_PRIMARY=groq` e reinício/redeploy; o padrão é
   `mistral`.
6. O status registra `social_primary_provider` para comprovar
   roteamento efetivo e evitar suposição por variáveis ou logs.
7. Resposta do Mistral **só chega à voz após validação integral**
   local de JSON, tipos de moves, fechamento e profundidade. O
   endpoint hospedado usa instruções para produzir JSON, mas seu
   contrato estrito de schema ainda é não verificado. O descriptor
   `supports_structured_output=true` nesta rota significa
   **adaptação JSON + validação estruturada local obrigatória**,
   não suporte garantido do endpoint a `response_format=json_schema`.

## Limites conscientes

A latência de Mistral observada nos testes alcançou dezenas de
segundos, às vezes mais de um minuto. Não é ideal para turnos de
voz em tempo real. O adapter produtivo limita o tempo total da chamada
a 95s (55s entre eventos úteis) e **não repete silenciosamente**
gerações caras antes de deixar a execução cair no fallback. Infra
HTTP/proxies e orçamento por turno podem impor limites menores.

Revisão humana e certificação completa continuam recomendáveis para
monitoramento contínuo, **sem reabrir a escolha de primary** sem
instrução explícita do usuário. Falha no provider, vazamento de dados,
saída malformada ou desvio de identidade não são aceitos como preço
da liberdade de vocabulário.
