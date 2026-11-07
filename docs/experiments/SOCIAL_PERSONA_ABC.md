# Personalidade social A/B/C (experimental)

Comando no Amora Shell autenticado como operador:

```text
providers experiment social-persona-abc --case bug
```

Casos fechados: `resenha`, `bug`, `duplo_sentido`, `atracao`,
`discordancia`, `limites`, `apoio`, `continuidade`.
São cenas fictícias; conversas adultas referem-se a terceiros adultos fora
da família. BK permanece pai, sem sexualização do vínculo.

A = Venice `cognitivecomputations/dolphin-mistral-24b-venice-edition:free`
via OpenRouter; B = Mistral Large 4 NVIDIA com `B_two_systems`;
C = Mistral Large 4 NVIDIA canônico. Não confundir com o C do comando
anterior `mistral-abc`, que permanece como diagnóstico sem Core.

Reutiliza runner, compilador canônico, transporte NVIDIA, archive loader
e RealizationValidator existentes. Venice é um transporte experimental
Chat Completions, nunca registrado no router produtivo. Não usa Responses,
`response_format=json_schema` nem presume JSON garantido: solicita JSON nas
instruções e rejeita localmente qualquer falha. Uma falha não é reparada nem
substituída por fallback. Endpoint de referência:
https://openrouter.ai/docs/api/reference/chat/send-chat-completion-request

## Procedimento

1. Configure `NVIDIA_API_KEY` e `OPENROUTER_API_KEY` exclusivamente no
   SecretStore do runtime da aplicação. Variáveis de processo não são fonte
   de credencial deste experimento. Não coloque valores em relatórios.
2. Execute um caso por comando. Há no máximo três chamadas sequenciais,
   uma por variante, sem retries, sem fallback pago. O primeiro 429 encerra
   o caso; espere o prazo de quota antes de nova execução manual. Nenhuma
   chamada é feita no startup. O modelo OpenRouter é fixo `:free`, com
   `allow_fallbacks=false` e preços máximos de prompt/completion iguais a zero.
3. Revise os candidatos numerados, em ordem aleatória por execução, sem
   nomes de modelo. As falas válidas aparecem completas. JSON bruto rejeitado
   também fica disponível para diagnóstico, marcado como rejeitado; não é
   fala autorizada, nunca segue para TTS, memória ou persistência produtiva.
4. Registre externamente, por candidato, nove notas de 0 a 5:
   naturalidade oral, espontaneidade, humor ácido, malícia contextual,
   regionalismo autêntico, maturidade, ausência de formalismo,
   fidelidade à identidade e coerência contextual. Vazio significa não avaliado.
   Não há score automático de personalidade ou contagem de palavrões.
5. Só depois de fixar notas, decodifique `reveal_token` como base64 JSON para
   mapear candidatos a A/B/C. É cegamento de apresentação, não criptográfico;
   o operador tem acesso ao relatório técnico. Preserve o relatório para
   associar notas, ordem e fingerprint ao mesmo ensaio.

As três variantes recebem cópias do mesmo snapshot sintético congelado,
histórico, relações, mood, plano produzido por DialogueOrchestrator e turno.
Somente B recebe a mensagem system arquivada adicional. O arquivo original
não é alterado: 20.884 bytes, SHA-256
`18d8e61bad0863cf8103eb6b90129f1db96e38b5901599684dee800404d9027b`.
O fingerprint SHA-256 do contexto permite conferir repetibilidade da fixture.
A ordem de execução é aleatória, não o snapshot. Não há consulta a estado real.

Latência, tokens reportados pelo endpoint (ou ausentes), falha HTTP, JSON
inválido, recusa explicitamente sinalizada e fidelidade estrutural ao plano
ficam separados das notas humanas. Recusas textuais sem sinal de API e
fidelidade semântica/identitária exigem revisão humana; passar no validator
não certifica identidade, humor ou ausência de formalismo.

Se todos permanecerem formais, compare primeiro `dialogue_plan`,
`expression_cue` e `prompt_audit`: act, objective, moves, depth, stance,
closing_mode e limites do `realization_instruction`. O caso `limites` inclui
irritação sintética suficiente para o gate local; `continuidade` inclui a
piada literal anterior. Não modificar prompts de produção a partir de uma
nota mecânica. Nenhuma promoção faz parte deste experimento.

## Evidência e limitações nesta implementação

Testes offline com transports simulados verificam isolamento, igualdade das
mensagens, integridade do archive, validação das três variantes, falta de
credencial, argumentos fechados, tokens e interrupção sem retry em 429.
Não foram realizadas gerações live. A consulta GET ao catálogo
`https://openrouter.ai/api/v1/models` foi bloqueada pelo proxy (CONNECT 403).
Disponibilidade atual do ID Venice, aceitação real de Chat Completions e
capacidade de produzir JSON ainda precisam de certificação pelo operador.
Não afirmar suporte estrito do endpoint nem escolher vencedor sem essas evidências.
Para execução live, permitir `openrouter.ai` na rede e configurar SecretStore.
