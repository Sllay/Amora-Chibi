# AMORA — Identidade Vocal Canônica

**Status:** CANÔNICO / ESPECIFICAÇÃO DE ALVO  
**Atualizado em:** 2026-10-11  
**Runtime atual:** sherpa-onnx + Piper/VITS provisional Faber  
**Alvo final:** `amora-voice-v1` feminina própria, local/offline e rights-clear.

---

## 1. Princípio

> A voz é parte da identidade contínua da Amora.

O backend de TTS pode mudar no futuro, mas a identidade vocal percebida não pode variar aleatoriamente entre providers, sessões ou estados emocionais.

A voz final deve funcionar localmente no Galaxy A15 sem depender de quota remota.

---

## 2. Perfil vocal alvo

A voz canônica da Amora deve soar como:

- feminina;
- brasileira / pt-BR natural;
- jovem adulta;
- doce sem infantilização;
- madura o suficiente para não soar como voz de anime/chibi exageradamente aguda;
- ainda jovem, sem peso de voz muito madura/mais velha;
- região base entre médio e médio-grave, com brilho/agudos naturais quando a expressão pedir;
- dicção clara, mas não de narradora, locutora, assistente ou anúncio;
- ritmo base moderado, ligeiramente vivo/brasileiro, sem falar acelerado por padrão;
- energia presente, espontânea e conversacional, sem hiperatividade artificial;
- microvariações naturais de ritmo, pausa e intensidade;
- capaz de intimidade, brincadeira, firmeza, baixa energia e irritação sem perder o mesmo timbre-base.

Não é objetivo imitar uma pessoa pública ou voz sem direitos claros.

---

## 3. Identidade fixa vs expressão dinâmica

### 3.1 Identidade fixa

Deve permanecer reconhecível em todos os estados:

- timbre-base;
- faixa aparente de idade;
- sotaque/base fonética pt-BR;
- peso/brilho característicos;
- ressonância;
- assinatura de dicção;
- identidade do speaker/modelo.

### 3.2 Expressão dinâmica

Pode variar dentro de limites pequenos e controlados:

- rate;
- energia;
- duração de pausas;
- intensidade;
- firmeza;
- suavidade;
- pitch delta pequeno quando tecnicamente suportado sem deformar timbre;
- timing/fraseado.

Emoção não escolhe diretamente parâmetros crus. O `Prosody Planner` recebe estado interno + intenção + tipo de fala e produz uma configuração bounded.

---

## 4. Mapeamento emocional

Exemplos conceituais, não fórmulas fixas:

### Baixa energia / tédio

- ritmo um pouco mais lento;
- pausas maiores;
- energia menor;
- respostas possivelmente mais curtas pelo DialoguePlan;
- sem transformar a voz em monotonia robótica.

### Alegria / animação

- energia e ritmo discretamente maiores;
- pausas mais leves;
- brilho natural;
- sem acelerar até parecer síntese artificial.

### Irritação / raiva

- comportamento social fica mais firme antes da voz mudar;
- wording pode ficar mais curto/direto conforme DialoguePlan;
- prosódia pode ganhar firmeza e tensão leve;
- não representar raiva simplesmente como pitch/volume altos.

### Afeto / intimidade

- ritmo levemente mais macio;
- pausas naturais;
- menor dureza;
- sem infantilizar a voz.

---

## 5. Modelo final

O alvo é um modelo single-speaker próprio compatível com inferência local, preferencialmente exportável para ONNX e utilizável no pipeline sherpa-onnx já comprovado.

Qualidade `medium` vs `high` não será escolhida pelo rótulo. A escolha final considera:

- naturalidade;
- artefatos metálicos/mecânicos;
- pronúncia pt-BR;
- consistência de identidade;
- tamanho;
- RTF;
- RAM/CPU/temperatura no Galaxy A15.

Um modelo maior é aceitável se a melhora de qualidade for perceptível e o A15 permanecer estável.

---

## 6. Dataset e direitos

Material de treino precisa ter origem e licença auditáveis.

Aceitável:

- gravação própria com consentimento explícito;
- dataset público cuja licença permita o uso pretendido e derivação/distribuição do modelo;
- material sintético apenas quando os termos da voz/fonte autorizarem explicitamente uso para treinamento/derivação.

Não aceitável como base canônica:

- voz de pessoa pública sem autorização;
- voz comercial/API cujos termos proíbam treinamento/derivação;
- dataset de origem obscura;
- voice clone sem consentimento/direitos claros.

A provenance do dataset deve ser registrada junto da versão do voice pack.

---

## 7. Estratégia de compute

Objetivo operacional: **R$ 0 sempre que praticável**.

Ordem de preferência para treino/fine-tuning:

1. GPU gratuita temporária disponível em notebook/serviço acadêmico/comunitário;
2. hardware gratuito já acessível;
3. alternativas de treino mais leves/checkpoint/fine-tuning que reduzam VRAM;
4. apenas após evidência de necessidade, avaliar compute pago com teto de custo explícito.

Treinar no Galaxy A15 **não é o plano padrão**. RAM do celular não equivale a VRAM de GPU, e treino intenso traz risco térmico, tempo excessivo e desgaste. O A15 é o alvo de **inferência e certificação**, não a máquina principal de treinamento.

Nenhum serviço pago deve ser deixado rodando sem limite/teto verificável.

---

## 8. Pipeline final esperado

```text
dataset rights-clear
        ↓
preparo / limpeza / metadata
        ↓
fine-tuning / treinamento fora do A15
        ↓
export ONNX
        ↓
validação de licença + hash + qualidade
        ↓
Voice Pack amora-voice-v1
        ↓
sherpa-onnx Android
        ↓
Prosody Planner bounded
        ↓
PCM / AudioTrack
```

---

## 9. Estado atual

O Faber masculino permanece somente como **voz provisória técnica** que certificou o caminho local.

Ele não define a identidade vocal final da Amora.

A criação/treino de `amora-voice-v1` ocorre na fase final da V1, depois de Dialogue Identity, Emotion wiring e provider resilience estarem estruturalmente fechados.
