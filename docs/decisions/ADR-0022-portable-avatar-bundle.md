# ADR-0022 — Portable Avatar Bundle

**Status:** ACCEPTED  
**Data:** 2026-10-30

## Contexto / problema

O Avatar Studio exportava somente `project.json`. Esse arquivo preservava a configuração lógica do Avatar, mas não carregava os binários referenciados em `project.assets`. Na prática, o backup podia restaurar layers, transforms, rig, expressões e animações e ainda assim perder corpo/texturas caso os assets não existissem mais no storage da instância.

O Core já possuía um ZIP sidecar para backup operacional (`<snapshot>.avatar.zip`), porém esse arquivo é vinculado ao snapshot SQLite e não foi desenhado como um formato portátil de modelo para o Studio.

BK decidiu que o backup normal do Avatar deve ser um único ZIP autossuficiente, com manifest, projeto e assets.

## Método anterior

```text
Studio → Exportar JSON → project.json
```

O JSON guardava referências de assets, não seus bytes. Importar esse arquivo dependia do storage antigo ainda conter as texturas.

## Alternativas consideradas

1. **Continuar JSON + pedir ao usuário para copiar assets separadamente.** Rejeitado: fácil criar backup incompleto e difícil verificar integridade.
2. **Montar/desmontar ZIP inteiramente no navegador com biblioteca nova.** Rejeitado: duplicaria regras de persistência/validação no adapter Web e adicionaria dependência desnecessária.
3. **Reutilizar diretamente o sidecar do backup SQLite.** Rejeitado: ele é um artefato de disaster recovery ligado ao banco, não um contrato portátil de um Avatar.
4. **Core gerar/validar um bundle portátil versionado.** Aceito.

## Decisão

Criar o formato `amora-avatar-bundle` v1, gerado e validado pelo Core.

Estrutura:

```text
amora_avatar.zip
├── manifest.json
├── project.json
└── assets/
    └── <asset files>
```

### `project.json`

É a serialização canônica completa de `AvatarProject`. Portanto preserva todo estado não-binário atualmente pertencente ao modelo:

- canvas;
- color slots;
- asset refs;
- parts/layers;
- transforms e pivôs;
- sockets/attachments;
- bones;
- IK;
- physics;
- expressions;
- animations/tracks/keyframes;
- metadata.

### `manifest.json`

Registra:

- `format` e `version`;
- id/schema/path/tamanho/SHA-256 do projeto;
- para cada asset: id, nome, MIME, caminho, tamanho e SHA-256;
- contagens estruturais do modelo para inspeção humana/diagnóstico.

### Endpoints

```text
GET /v1/avatar/bundle
PUT /v1/avatar/bundle
```

Ambos usam a mesma autenticação `admin ou avatar` já aplicada às APIs do Studio.

### Ownership

O Core é dono do formato, integridade e restore. O navegador apenas inicia download/upload e exibe resultado. Nenhuma regra de modelo, rig, persistência ou integridade é movida para o frontend.

### Validação antes de mutação

O import valida completamente o arquivo antes de substituir estado:

- ZIP válido;
- versão/formato suportados;
- limites de tamanho e quantidade de entries;
- sem paths absolutos, `..`, backslashes ou entries criptografadas;
- `manifest.json` e `project.json` presentes;
- SHA-256 e tamanho do projeto;
- `AvatarProject` válido;
- v1 direcionado ao projeto ativo `default`;
- conjunto do manifest idêntico a `project.assets`;
- todas as `parts` referenciam assets declarados;
- MIME permitido;
- SHA-256 e tamanho de cada asset;
- nenhum arquivo não listado no manifest.

Apenas depois dessa validação os assets são aplicados e, por último, o projeto é salvo.

### Falha durante aplicação

Antes de escrever cada asset, o import mantém a versão anterior em memória. Uma falha durante gravação tenta compensar explicitamente qualquer asset já tocado. Se a gravação final do projeto falhar ou tiver resultado remoto ambíguo, o Core tenta restaurar os assets anteriores e regravar o projeto anterior.

Essa compensação é best-effort porque `FileBlobStore` e armazenamento SQL não compartilham uma transação física única. Falha também durante a compensação é tratada como erro operacional grave; não é reportado sucesso falso.

### Compatibilidade

- **Export:** somente ZIP portátil no Studio.
- **Import:** ZIP portátil é o caminho normal.
- JSON antigo permanece aceito apenas como fallback de restauração para exports já existentes; ele não ganha capacidade de recriar assets ausentes.
- O backup operacional `amora backup` + `.avatar.zip` continua separado e inalterado.

## Motivos

- backup realmente autossuficiente;
- integridade verificável;
- nenhuma dependência npm/JS adicional;
- Core permanece autoridade de schema/persistência;
- formato versionado e extensível;
- animações e demais dados estruturais seguem junto automaticamente por pertencerem ao `AvatarProject`;
- um arquivo é mais simples de mover entre dispositivos/instâncias.

## Consequências

- Export falha explicitamente se um asset declarado pelo projeto estiver faltando ou corrompido; não gera bundle silenciosamente incompleto.
- Bundles v1 recusam arquivos extras não declarados. Novos tipos de conteúdo binário fora de `assets/` devem elevar/evoluir o formato conscientemente.
- O ZIP pode ser maior que o JSON; limites impedem abuso/ZIP bomb.
- Nenhuma migration de banco é necessária.
- O bundle portátil não substitui disaster recovery do Core inteiro.

## Critério para revisitar

Revisitar quando ocorrer pelo menos um dos casos:

- múltiplos projetos Avatar ativos;
- novos dados binários além de texturas/assets;
- necessidade real de import transacional cross-store mais forte;
- assinatura/autenticidade criptográfica de bundles;
- mudança incompatível do schema do Avatar que exija migração explícita dentro do arquivo.

## ADRs relacionados

- ADR-0015: fronteiras de Core remoto/storage portátil.
- ADR-0020: falhas remotas, outcome unknown e recuperação fail-closed.
- ADR-0021: semântica de writes atômicos single-statement no storage remoto.
