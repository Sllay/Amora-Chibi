# ADR-0040 — Perspectiva contextual de referências em texto livre

Status: ACCEPTED
Data: 2026-11-08
Supersedes: none; complementa ADR-0038/0039.

## Contexto e evidência

“A sua mae mim amar”, de BK para Amora, não tinha referências no ACIR
compilado. A projeção de relações filtrada pelo speaker também omitia a mãe
canônica da Amora. O resolver unitário sabia interpretar possessivos, mas o
provider real recebia o texto sem essa anotação e sem a aresta necessária.
Não foi reproduzida uma chamada remota; a falha estrutural foi reproduzida
no Core e no payload NVIDIA anterior à correção.

## Método anterior e alternativas

O envelope conservador resolvia apenas eu/você. Substituir “sua mãe” por um
nome, promover o literal ao system, enviar o grafo inteiro ou delegar tudo a
outro LLM foram rejeitados: perdem perspectiva, privacidade ou determinismo.

## Decisão

Anotação lexical limitada reutiliza o resolver e a atribuição de discurso.
Offsets preservam a entrada informal; IDs/reason codes são dados estruturados.
Primeira pessoa acompanha o autor; segunda pessoa acompanha o destinatário.
Discurso explicitamente citado não herda silenciosamente o destinatário externo.
Terceira pessoa sem antecedente confiável conserva ambiguidade. A gramática
atual cobre mãe/pai/filho/filha; não promete compreensão geral de português.

O registry usado para resolver tem somente arestas do destinatário, parentesco
do catálogo canônico inicial e arestas respaldadas por claims canônicas que
passaram pelo privacy gate. Resolver não cria arestas. Identificar uma pessoa
não confirma um sentimento: alegações permanecem não verificadas e não ganham
autorização de persistência.

O navegador envia uma sessão aleatória por aba, sem transcript ou credencial
no identificador. Histórico de teste só participa de outro teste da mesma
sessão explícita; não passa a alimentar turnos produtivos.

## Consequências e revisão

Sem migration, novo parser ou custo remoto. Payloads ganham anotações, com
orçamento ainda aplicado ao envelope final. Ambiguidade conserva informação
para o realizador; não certifica que um modelo remoto irá respeitá-la.
Revisitar com exemplos reais de discurso aninhado, outros parentescos e
antecedentes conflitantes; ampliar estruturas, não colecionar frases especiais.
