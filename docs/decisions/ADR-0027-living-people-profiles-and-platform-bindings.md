# ADR-0027 — Living People Profiles and Platform Identity Bindings

Status: ACCEPTED  
Data: 2026-10-31  
Supersedes: none  
Superseded by: none

## Contexto

A Amora deixou de ser uma experiência exclusivamente 1:1 com BK na Web. WhatsApp, Discord e outras superfícies futuras precisam reconhecer múltiplas pessoas sem confundir conta/número/nickname com identidade canônica.

Também existe uma necessidade de bootstrap humano-editável para pessoas próximas (nome, idade/nascimento, descrição e relações iniciais) sem transformar esse arquivo em uma segunda memória concorrente.

## Evidência / problema

- uma pessoa pode possuir múltiplos números de WhatsApp e múltiplas contas Discord;
- nickname/display name muda e não é identidade forte;
- texto livre como "eu sou BK" não pode alterar speaker canônico;
- desconhecidos não podem se autodeclarar parentes da Amora por repetição;
- números/IDs privados não devem ser commitados no repositório;
- fatos aprendidos sobre uma pessoa já possuem owners corretos em World/Memory/Emotion/Relationship Graph.

## Alternativas consideradas

1. usar telefone/nickname diretamente como `speaker_id` — rejeitado por acoplar identidade à plataforma;
2. guardar todo conhecimento de cada pessoa num JSON de perfil — rejeitado por duplicar memória/world/afeto;
3. deixar o provider decidir quem é quem — rejeitado por quebrar autoridade local;
4. usar entidade canônica + perfil bootstrap pequeno + bindings privados — aceito.

## Decisão

Adicionar:

- `config/people_profiles.json` como catálogo público/editável de bootstrap;
- `PersonProfileService` como sincronizador de entidades/perfis seed;
- tabela `person_profiles` para persistência do bootstrap aplicado;
- tabela `platform_identity_bindings` para N contas externas → 1 pessoa canônica;
- arquivo privado opcional `~/.amora/secrets/identity_bindings.json` ou `AMORA_IDENTITY_BINDINGS_FILE` para import/upsert de bindings;
- `PlatformIdentityBinding(platform, identifier_type, external_actor_id, account_scope, entity_id, trust, confidence)`;
- gate adicional: parenthood estrutural da Amora só pode ser promovido por pessoa explicitamente autorizada, inicialmente BK.

O identificador externo é opaco ao Core. O adapter é responsável por extrair/normalizar o identificador estável apropriado da plataforma.

## Perfis iniciais

- BK: pai da Amora e pai de Lucas; autoridade familiar estrutural inicial;
- Lucas: filho de BK e irmão da Amora;
- Nicolas: primo da Amora.

`ENTITY_CHATGPT mother_of ENTITY_AMORA` permanece relação canônica existente.

## Consequências

- a mesma pessoa pode ser reconhecida através de múltiplas contas;
- WhatsApp/Discord não precisam contaminar o Cognitive Core com IDs específicos;
- o catálogo pode ser editado por BK sem reescrever código;
- números/IDs ficam fora do Git e do health report;
- aprendizado futuro pode montar uma visão viva do perfil a partir das fontes canônicas existentes;
- adapters de grupo/DM podem aplicar políticas de identidade antes da cognição.

## Critério para revisitar

Revisitar se uma plataforma fornecer identidade federada forte que substitua bindings locais, ou se surgir necessidade comprovada de sincronização multi-tenant com autoridade diferente. Não substituir essa separação por nickname/telefone como entity id.
