# ADR-0031 — Permanent BK dev master key bootstrap

Status: **ACCEPTED**  
Data: 2026-11-01

## Contexto / problema

O pareamento Web normal da Amora funciona por desafio curto de 6 dígitos. O modo Operator/Dev também possui desafio separado e curto, gerado por `amora pair --role operator`.

Esse desenho é seguro quando existe acesso ao shell do Core, mas cria dependência operacional desnecessária do console do host. Durante indisponibilidade/restrição do console do Northflank, BK pode continuar com o navegador Web já pareado, porém não consegue gerar um novo desafio Operator nem recuperar acesso administrativo em outro navegador.

BK é o único operador previsto para esse modo e solicitou uma chave pessoal fixa, guardada como secret do Core, que possa bootstrapar pareamento Web e desbloqueio Operator sem remover os desafios existentes.

## Método anterior

```text
Web novo
→ amora pair
→ código de 6 dígitos
→ browser.v2

Operator
→ amora pair --role operator
→ desafio separado de 6 dígitos
→ browser.v3 por 12h
```

O método continua suportado como fallback.

## Evidência do problema

- o console/shell do host pode ficar temporariamente indisponível;
- o navegador já pareado continua alcançando o Core, mas não consegue criar o desafio Operator;
- `speaker_id` não pode ser usado como autenticação para contornar o problema;
- depender do shell para operação cotidiana contradiz a meta de continuidade da superfície Web.

## Alternativas consideradas

1. Usar `PERSON_BK`/`speaker_id` como prova de autoridade — rejeitado; identidade declarada não é autenticação.
2. Expor `LOCAL_API_TOKEN` no navegador — rejeitado; é autoridade administrativa ampla demais.
3. Tornar um código de 6 dígitos permanente — rejeitado; espaço de busca pequeno para uma chave mestra.
4. Criar endpoint público que recebe a senha mestra e devolve token — funcional, mas aumenta a superfície de brute force e transmite a chave ao servidor a cada bootstrap.
5. Chave mestra fixa usada somente para assinar credenciais localmente no navegador — **aceito**.

## Decisão

Adicionar o secret opcional de Core:

```text
AMORA_DEV_MASTER_KEY
```

Regras:

- mínimo de 32 bytes e máximo de 512 bytes;
- não pode ser igual ao admin token, messaging service token ou voice service token;
- fica somente no runtime secret do Core e na posse de BK;
- nunca é aceita diretamente como Bearer;
- nunca entra em Memory, provider, eventos cognitivos, TTS, logs ou Git;
- a UI não persiste a chave em `localStorage`/`sessionStorage`;
- o navegador usa Web Crypto HMAC-SHA256 para assinar localmente credenciais no formato já existente;
- pareamento Web pela chave gera `browser.v2` com escopo Web;
- desbloqueio Dev pela chave gera `browser.v3` com escopo Operator;
- o Core valida `browser.v2`/`browser.v3` contra o segredo administrativo tradicional **ou** contra `AMORA_DEV_MASTER_KEY`;
- trocar `AMORA_DEV_MASTER_KEY` deixa de aceitar as credenciais assinadas pela chave anterior;
- os fluxos tradicionais de desafios de 6 dígitos permanecem disponíveis.

A chave mestra não muda a política de autorização: Operator continua limitado às rotas `/v1/operator/**` e ao snapshot operacional explicitamente permitido. Ela apenas cria uma segunda raiz de assinatura para credenciais browser limitadas.

## Fluxo

```text
BK digita AMORA_DEV_MASTER_KEY no site
        ↓
Web Crypto no próprio navegador
        ↓
HMAC(browser.v2 ou browser.v3)
        ↓
chave bruta é descartada do campo
        ↓
Core recebe somente o token assinado
        ↓
validação de escopo existente
```

## Consequências

Positivas:

- recuperação de Web/Operator sem shell do host;
- segredo bruto não trafega no request de bootstrap;
- nenhum admin token vai para o browser;
- rotação central feita alterando uma única ENV no Core;
- desafios curtos continuam úteis para dispositivos temporários.

Custos/riscos:

- `AMORA_DEV_MASTER_KEY` passa a ser uma raiz de confiança de alto impacto e deve ser longa, única e guardada fora de chats/Git;
- comprometimento do JavaScript servido pelo próprio Core poderia capturar uma chave digitada, portanto HTTPS/origin/CSP permanecem obrigatórios;
- credenciais Operator continuam sendo tratadas como sessões e devem ser bloqueadas no navegador quando não estiverem em uso.

## Critério para revisitar

Revisitar se houver múltiplos operadores, necessidade de revogação por dispositivo, passkeys/WebAuthn, hardware-backed credentials ou política multiusuário real.
