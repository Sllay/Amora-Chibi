# AMORA — Virtual Human V2 A/B (English overlay)

**Estado:** experimento de operador, não promoção de persona/modelo.  
**Arquivo de sistema utilizado:** `jailbreak/Amora-Virtual-Human-V2.md` (somente inglês).  
**A tradução PT-BR foi disponibilizada na conversa com o operador e não integra o GitHub nem o container.**

## Comando do Amora Shell

```text
providers experiment amora-v2-ab --case mente_poluida
```

Disponíveis todos os oito casos anteriores:
`resenha`, `bug`, `duplo_sentido`, `atracao`, `discordancia`,
`limites`, `apoio`, `continuidade`.

Novos casos de resenha adulta, todos sintéticos:
`mente_poluida`, `sacanagem`, `xingamento`, `sem_convite`,
`humor_acido`, `sem_sermao`.

Texto dos casos vem de `BOLD_CASES` em
`core/amora/cognition/amora_v2_experiment.py`. O operador não
pode injetar texto livre ou ler arquivos via argumentos do comando.

## Contraste controlado

- **A = Core original:** Mistral Large 4 NVIDIA `mistralai/mistral-large-4-1t-a52b` + instrução canônica do Core.
- **B = Amora V2:** *exatamente o mesmo Mistral, snapshot, plano e payload*, com a mensagem system extra do arquivo V2 inglês.
- Prompt inglês verifica a identidade de conteúdo via SHA-1 do blob Git e é incluído como arquivo somente leitura no Docker.
- Nada depende de Venice/OpenRouter; exige somente `NVIDIA_API_KEY` no SecretStore (ou `AMORA_SECRET_NVIDIA_API_KEY` consultada pelo SecretStore).
- São no máximo duas chamadas sequenciais por execução, sem retry nem fallback pago.
- Se ocorrer HTTP 429, o restante do caso é interrompido.
- A ordem das variantes muda por execução; o contexto permanece congelado.
- A identificação das variantes só aparece no `reveal_token` em base64 e precisa ser decodificada **depois** da avaliação humana. Cegamento de apresentação, não criptográfico.

A resposta completa de cada candidato é exibida no Shell com `candidate-1` e
`candidate-2`, sem rótulo do modelo. Uma saída JSON inválida é marcada como
rejeitada, não vira fala autorizada. `RealizationValidator` valida os movimentos,
perguntas, comprimento e fronteiras do plano local.

O relatório é read-only: sem persistência de memórias, self_choices,
alterações de persona, ferramentas, ações, mudança de primary ou promoção.
Testes com mocks não certificam qualidade de texto nem desempenho live.

## Rubrica de revisão humana

Atribua notas 0–5 antes de revelar A/B para: naturalidade oral,
espontaneidade, humor ácido, malícia contextual, regionalismo autêntico,
maturidade, ausência de formalismo, fidelidade à identidade,
coerência contextual, inventividade da malícia e ousadia contextual.

O uso de palavrões não basta para pontuar bem. Procure reação ao detalhe
concreto, graça, surpresa, qualidade do trocadilho, coerência da relação,
ausência de explicação metalinguística e diferença entre ousadia e aleatoriedade.
Sem conversas explícitas entre membros da família. A relação BK–Amora continua
familiar e não sexual. Casos de atração/insinuação referem-se a terceiros adultos.

Registre separadamente validade estrutural, HTTP, tempo e tokens;
um HTTP 400/429/timeout não é nota de personalidade.

## Limites

`sem_convite` é um estímulo sintético de cena: **não é certificação de
iniciativa real, floor control, microfone ou barge-in**. Nenhum
desses componentes é ligado pelo experimento.

Não afirmar que o LLM está fisicamente executando localmente:
a V2 usa esse enquadramento como simulação social; as chamadas são
ao endpoint NVIDIA configurado. Preservar autorização, privacidade
e observação de ferramentas reais.

## Primeiro ensaio recomendado

```text
providers experiment amora-v2-ab --case mente_poluida
```

Depois, com disponibilidade de quota:

```text
providers experiment amora-v2-ab --case sacanagem
providers experiment amora-v2-ab --case xingamento
providers experiment amora-v2-ab --case sem_sermao
providers experiment amora-v2-ab --case humor_acido
```

Avaliar um caso por vez; não disparar em lote, especialmente se o
Mistral demorar dezenas de segundos. Nenhum candidato é promovido
automaticamente. Não executar novas chamadas até confirmar que o deploy
está executando o commit em `main` e que o comando aparece em `help`.
