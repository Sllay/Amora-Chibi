# Amora Avatar Studio

Editor visual mobile-first + runtime web compartilhado da Amora.

A V0.9 usa o mesmo `engine.js` nos dois modos:

- **Studio**: edição por touch de parts, pivôs, layers, bones, IK, cores, expressões, physics e timeline;
- **Runtime**: carrega o mesmo projeto e traduz o estado semântico do Core em animação visual.

O JSON é apenas a serialização automática do projeto. O fluxo normal não exige editar coordenadas ou hierarchy manualmente.

## Abrir no A15

Com o Core rodando:

```bash
amora serve --port 8765
```

Studio:

```text
http://127.0.0.1:8765/avatar/
```

Runtime:

```text
http://127.0.0.1:8765/avatar/?mode=runtime
```

Se o navegador ainda não estiver pareado:

```bash
amora pair
```

Digite o código de 6 dígitos no modal do Studio.

## Abrir em outro celular na mesma rede

`127.0.0.1` é loopback e só funciona no próprio A15. Para montar o avatar em outro celular que contém os sprites, use o servidor LAN dedicado do Avatar:

```bash
amora-avatar serve --lan --port 8765
```

O comando escuta em `0.0.0.0`, tenta descobrir o IP LAN do A15 e imprime a URL para abrir no outro aparelho, por exemplo:

```text
http://192.168.1.42:8765/avatar/
```

Os dois aparelhos precisam estar na mesma Wi-Fi/hotspot. O modo LAN é intencionalmente restrito: pela rede ele libera somente o shell `/avatar/`, APIs `/v1/avatar/*`, resgate do código de pareamento e `/health`; as demais rotas do Core retornam 404.

Para apenas descobrir URLs sem iniciar outro servidor:

```bash
amora-avatar url --port 8765
```

Use o modo LAN somente em Wi-Fi/hotspot confiável. O pareamento de 6 dígitos continua obrigatório para as APIs autenticadas do Studio.

## Rig v2: sockets + bones/IK

A V0.9 aceita dois fluxos ao mesmo tempo:

```text
peça rígida (ex.: braço inteiro)
Torso socket → Arm socket → Hand socket

membro segmentado (ex.: coxa/perna/pé)
Bones + IK tradicional
```

Sockets são pontos normalizados dentro da textura. Uma conexão prende o socket da peça filha ao socket da peça parent e herda rotação. Isso é ideal para braço inteiro, mão, cabeça, acessórios e outras partes rígidas.

Bones e IK não foram removidos. Eles continuam disponíveis para membros divididos em duas ou mais partes, como perna e futuros braços upper-arm/forearm.

Cada peça também possui:

- `Flip H` e `Flip V` sem destruir escala/pivô;
- `locked` para impedir edição acidental;
- `pickable/click-through` para layers da frente não roubarem seleção;
- múltiplos sockets nomeados.

### Regra de seleção

O Studio separa escolha de manipulação:

```text
Selecionar → hit-test normal, layer mais alta primeiro
Mover      → usa a peça já selecionada
Pivô       → usa a peça já selecionada
Girar      → usa a peça já selecionada
Socket     → usa o socket já selecionado
```

Isso evita que cabelo/face/layers superiores roubem o gesto quando o usuário está editando uma peça escolhida pela lista.

### Compatibilidade com projetos já mapeados

Rig v2 é uma extensão aditiva do projeto. Projetos `schema_version: 1` continuam válidos. Ao carregar um projeto antigo, nomes, IDs, layers, transforms, pivôs, tags, color slots, expressões, bones e animações são preservados; os campos novos recebem apenas defaults (`anchors=[]`, `attachments=[]`, flips desligados, layer livre e selecionável).

O schema só sobe para 2 quando uma edição Rig v2 é salva. Não existe reset automático do projeto nem renomeação automática de peças já mapeadas.

## Fluxo visual

```text
importar sprites
→ nomear/organizar layers
→ ajustar pivôs
→ peça rígida: criar sockets e conectar
→ membro segmentado: mapear bones e IK
→ configurar slots de cor
→ configurar face/tags
→ adicionar physics onde necessário
→ animar na timeline
→ salvar
→ abrir Runtime
```

## Controles touch

- um dedo sobre uma peça/handle: seleciona ou move conforme a ferramenta;
- arrastar fundo: pan;
- dois dedos: pan + pinch zoom;
- `Mover`: move somente a peça selecionada;
- `Pivot`: posiciona o pivô somente da peça selecionada;
- `Girar`: gira visualmente em torno do pivô;
- `Socket`: posiciona o socket escolhido diretamente na arte;
- `Bones`: move articulações;
- `IK`: arrasta targets de mãos/pés;
- bottom sheet concentra propriedades em telas pequenas.

O layout funciona em portrait e landscape. Em telas maiores aparece também inspector lateral.

## Assets e customização

Sprites brancos/alpha podem apontar para color slots como:

```text
skin
hair_primary
hair_secondary
eyes
dress
dress_straps
shoes
accessory
blush
```

O import tenta inferir slots/tags/bones pelo filename. Tudo continua editável visualmente depois.

Assets ficam no runtime local:

```text
~/.amora/avatar/assets/
```

## Backup

A partir da V0.9:

```bash
amora backup --name checkpoint.db
```

produz:

```text
checkpoint.db
checkpoint.db.avatar.zip
```

O sidecar contém os assets binários e é validado por tamanho + SHA-256 contra o snapshot SQLite.

## Testes locais do engine

O engine não exige dependências npm:

```bash
npm --prefix avatar test
```

A CI também executa `node --check` em `engine.js`, `app.js`, `rig-v2.js` e `sw.js`.

Mais detalhes: `docs/V0.9_AVATAR.md`.
