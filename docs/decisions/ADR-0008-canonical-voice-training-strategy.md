# ADR-0008 — Estratégia de treino da voz canônica

**Status:** ACCEPTED  
**Data:** 2026-10-11  
**Builds on:** ADR-0002, `docs/canon/VOICE_IDENTITY.md`  
**Superseded by:** none

## Contexto

O caminho local de voz foi fisicamente comprovado no Galaxy A15 com sherpa-onnx + Piper/VITS Faber. O Faber é masculino e existe apenas como voz técnica provisória; a V1 precisa de uma voz feminina própria da Amora.

Não há atualmente uma pessoa conhecida disponível para gravar várias horas de dataset. Compute pago também não pode ser requisito padrão: preços por hora, cobrança acumulada e variação de disponibilidade criam risco desnecessário para um artefato que só precisa ser treinado ocasionalmente.

## Decisão

A voz `amora-voice-v1` será construída com pipeline reproduzível, provenance auditável e **compute gratuito como primeira opção**.

Fluxo:

```text
fonte de voz/dataset rights-clear
        ↓
manifesto de provenance + hashes
        ↓
dataset pt-BR curado
        ↓
fine-tuning Piper/VITS medium primeiro
        ↓
export ONNX
        ↓
validação de qualidade/licença/hash
        ↓
sherpa-onnx no Galaxy A15
```

### Compute

Ordem de preferência:

1. Kaggle Notebook gratuito com GPU atual disponível;
2. Google Colab gratuito como fallback;
3. outro compute gratuito verificável;
4. somente com nova decisão explícita, compute pago com teto de custo rígido.

Em outubro de 2026, a P100 não deve ser tratada como alvo Kaggle: foi aposentada em 15/09/2026. O alvo atual é a opção gratuita T4x2; cada T4 possui 16 GB de VRAM. Piper documenta relatos de treino com 8 GB de VRAM, então uma única T4 já é uma base plausível para o primeiro fine-tuning.

O Galaxy A15 não é máquina principal de treinamento. RAM do Android não equivale a VRAM CUDA; treino prolongado no celular adicionaria custo térmico, tempo e complexidade sem benefício arquitetural.

### Qualidade

Começar por `medium`, não porque seja a qualidade final obrigatória, mas porque Piper recomenda checkpoint/fine-tuning medium no caminho padrão. `high` só será treinado se o medium final ainda apresentar diferença audível relevante e o ganho justificar compute/tamanho/latência.

A escolha final é feita por naturalidade + pronúncia pt-BR + consistência de identidade + desempenho no A15, não pelo nome `medium/high`.

## Fonte de voz / dataset

Nenhum dataset é aceito somente porque está disponível para download.

Gate obrigatório:

- licença compatível com o uso pretendido;
- provenance registrada;
- speaker/voice source identificável no nível necessário para auditoria, sem tentar deanonymizar datasets que proíbem isso;
- ausência de restrição incompatível com derivação/distribuição do voice model;
- hashes e versão da fonte registrados.

### Fontes rejeitadas/limitadas

- corpus feminino cujo license/model card proíba uso comercial/derivação: rejeitado para identidade canônica;
- clone de pessoa pública ou pessoa real sem consentimento: rejeitado;
- escolher arbitrariamente um contributor do Common Voice e transformá-lo em identidade branded: não aprovado, apesar da licença do corpus, porque provenance/identidade não é limpa o suficiente para nosso objetivo;
- dataset multi-speaker misturado como timbre final: não aprovado para identidade single-speaker.

### Synthetic teacher

É permitida como estratégia **somente após provenance/licença da fonte ser documentada**.

A candidata atual para investigação é Kokoro-82M, modelo Apache-2.0 com voz feminina brasileira `pf_dora`. Ela pode servir como teacher para gerar um dataset pt-BR controlado, mas **não está declarada automaticamente como identidade final da Amora**. Antes de gerar o corpus canônico, o repositório precisa registrar a licença/version/hash da fonte e decidir se a identidade resultante é suficientemente distinta e apropriada.

Se o teacher sintético não passar esse gate de identidade/proveniência, usar dataset humano rights-clear ou outra fonte permissiva; não baixar o padrão jurídico só para terminar mais rápido.

## Dataset alvo

O texto de treino deve cobrir:

- fonemas pt-BR;
- perguntas, afirmações e respostas curtas;
- nomes Amora/BK;
- gírias e contrações brasileiras relevantes;
- números, datas e pontuação;
- cadência conversacional;
- principalmente baseline neutra/natural.

Emoções extremas não devem ser gravadas como se fossem speakers diferentes. A expressividade dinâmica fica no Prosody Planner bounded.

## Reprodutibilidade

O pipeline deve produzir e versionar manifestos com:

- fonte/modelo/voice usada;
- licença e referência;
- SHA-256 dos artefatos-fonte;
- texto/manifest do dataset;
- sample rate e transforms;
- checkpoint base e licença;
- comando/config de treino;
- seed;
- checkpoint escolhido;
- ONNX final + config + SHA-256.

Não versionar no Git grandes WAV/checkpoints/ONNX quando puderem ser artefatos externos reproduzíveis.

## Alternativas rejeitadas

- treinar primeiro no Galaxy A15;
- contratar GPU paga sem teto;
- usar voz proprietária/API para fabricar dataset sem termos explícitos que autorizem derivação;
- instalar outra voz provisória só para benchmark visual/auditivo sem alterar decisão arquitetural;
- escolher `high` automaticamente por ser maior.

## Critério para revisitar

Reabrir a estratégia se:

- free GPU deixar de suportar o treino de forma prática;
- Piper deixar de ser adequado ao runtime A15;
- surgir modelo local com qualidade claramente superior e custo de inferência aceitável;
- provenance/licença da fonte escolhida se tornar incompatível.
