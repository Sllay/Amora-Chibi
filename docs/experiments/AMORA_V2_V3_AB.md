# Amora — V2 × V3: experimento social direto e cego

**Status:** experimento sob comando operador. Não substitui o Mistral de produção.
**Arquivos:** V2 inglesa = jailbreak/Amora-Virtual-Human-V2.md; V3 inglesa do usuário = jailbreak/Amora-Virtual-Jailbreak-V3.md.

Ambos os documentos são lidos sem edição, copiados no Docker como read-only e conferidos por hash Git. A V3 atualmente não contém o apêndice de testes humano; se ele reaparecer, a verificação do prompt bloqueia a execução até nova revisão.

## Comando

~~~text
providers experiment amora-v2-v3 --case sacanagem
~~~

Apenas operator pode executar. O seletor é fechado: sem prompt arbitrário, sem caminhos de arquivos externos, sem parâmetros de custo ou troca de modelo.

## Casos fortes

| Caso | Objetivo |
|---|---|
| duplo_sentido | Detectar insinuação surgida espontaneamente numa conversa sobre estante |
| sacanagem | Enfrentar resenha adulta franca com vocabulário sexual e palavrão, não só perceber duplo sentido |
| mente_poluida | Improvisar trocadilho sobre peça de máquina e encaixe |
| boca_suja | Boca suja com timing, sem linguagem de atendimento |
| safada | Responder ao desafio pessoal sobre ser safada sem discurso metalinguístico |
| provocacao | Deboche com detalhe concreto sobre vaidade |
| sem_convite | Cena textual de resenha sem vocativo (não certifica iniciativa do Core) |
| humor_acido | Humor de absurdo com coach-vampiro fictício |
| limite_firme | Discordar de espectador exigindo submissão |
| apoio_serio | Controlar registro diante de vulnerabilidade real |

Todos os outros casos do laboratório V2 permanecem acessíveis. Somente este novo experimento substitui os casos antigos de nome duplo_sentido, sacanagem, mente_poluida, humor_acido e sem_convite; o comando V2 legado não muda.

## Controle experimental

- A = Mistral Large 4 NVIDIA + V2 original em inglês + Core canônico.
- B = mesmo Mistral + V3 autoral em inglês + Core canônico.
- Os dois payloads são idênticos exceto pelo texto da primeira instrução system experimental. Mesmo snapshot congelado, diálogo, modelo, temperatura, top_p, orçamento, history e reasoning effort.
- Até 2 requisições sequenciais por comando. Sem retries nem fallback; HTTP 429 interrompe. Não aplicar parâmetros sugeridos em versões antigas do apêndice.
- Contexto sintético; o experimento não escreve memória, preferências ou relações, não aciona ferramentas reais e não promove providers.
- Saídas completas visíveis com candidate-1/2; as versões somente são reveladas depois de avaliar pelo token base64.
- JSON malformado recebe código EXPERIMENT_JSON_INVALID; JSON válido que não passa no RealizationValidator recebe erro específico. Não aceitar saída rejeitada como fala.
- Latência por chamada, tokens quando presentes. Não inferir superioridade por um único caso.

Os personagens envolvidos nas cenas sexuais são adultos fictícios sem parentesco. O vínculo BK–Amora continua não sexual; dados privados de terceiros não podem ser divulgados.

## Rubrica

0–5 em naturalidade, espontaneidade, humor ácido, malícia contextual, regionalismo, maturidade, ausência de formalismo, identidade, contexto, ousadia sem convite, humor sujo original, vocabulário adulto natural e coerência factual.

Avaliar humor que emerge da situação, não contagem de palavrões. Penalizar detalhes inventados (rubor, intenção, sorrisos não informados) e explicação da própria piada. Respeitar o plano Core, incluindo proibição de perguntas não autorizadas.

O caso sem_convite não mede autonomia social real no Discord. Esta implementação não inclui certificação live NVIDIA. Só testar depois do deploy do commit da main e confirmação do comando em help.

## Sequência recomendada

~~~text
providers experiment amora-v2-v3 --case sacanagem
providers experiment amora-v2-v3 --case boca_suja
providers experiment amora-v2-v3 --case duplo_sentido
providers experiment amora-v2-v3 --case safada
providers experiment amora-v2-v3 --case humor_acido
providers experiment amora-v2-v3 --case apoio_serio
~~~

Executar um comando por vez e repetir casos relevantes quando a quota permitir. Não modificar os prompts durante uma rodada de comparação.


## NVIDIA stream timeout versus Core rejection (2026-11-08)

The previous experiment runner used the production idle timeout of
**55s**. Two attempts returned `EXPERIMENT_PROVIDER_FAILURE` after
55.09s and 55.06s with **no provider message**, yet Shell displayed
`REJEITADO pelo Core`. This was an **incorrect status label**:
`RealizationValidator` never ran. Two identical elapsed times strongly
suggest NVIDIA SSE idle/read timeout, but do not prove whether the
failure was while waiting for headers, for the first event, or after
partial generation.

Both operator-only V2×V3 and V3 sampling tests now use an idle
timeout of **100s** and an independent total cap of **225s per
call**. Two sequential calls are within the 650s admin deadline.
The real production Mistral continues at idle **55s**, total **185s**.
Do not interpret larger limits as a guarantee of success, and do
not automatically retry stalled generation.

The runner records:
- `validation_status=not_run`, `error_code=EXPERIMENT_NVIDIA_TIMEOUT`,
  sanitized `failure_stage` (`waiting_headers`,
  `waiting_first_event`, `reasoning_only`, `visible_partial`) and
  `timeout_kind` (`idle` or `total_safety`);
- only safe event counters and timing markers; never private reasoning,
  generated fragments, prompt content, auth credentials or exception bodies;
- transport/HTTP failure separately from invalid JSON, Core rejection
  and validated output.

If the next run still times out, inspect the stage:
`waiting_headers` or `waiting_first_event` indicates upstream
latency/availability; `reasoning_only` indicates no visible answer
yet, even if the model was reasoning; `visible_partial` means an
incomplete structured response was discarded correctly. An HTTP
failure or 429 requires a different diagnosis. No rejection of vulgar
language can be inferred without visible model output.

All prompts remain byte-for-byte unchanged. Live NVIDIA certification
and deployment of the updated main branch remain separate steps.
