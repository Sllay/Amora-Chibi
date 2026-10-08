# Termux — bootstrap e compatibilidade

## Regra principal

O GitSync pode manter o repositório no armazenamento compartilhado do Android, mas o código executável, virtualenv, banco e secrets da Amora devem viver no filesystem privado do Termux.

## Bootstrap

```bash
bash /storage/emulated/0/Documents/Amora-Chibi/tools/termux-bootstrap.sh \
  /storage/emulated/0/Documents/Amora-Chibi
```

O script não depende de `rsync` e não altera o `pip` global do Termux.

## Layout

```text
/storage/emulated/0/Documents/Amora-Chibi   # GitSync
~/amora-src                                  # cópia executável
~/.venvs/amora                               # virtualenv
~/.amora                                     # runtime persistente
```

## Python 3.14 e pydantic-core

`pydantic-core` contém código Rust e o PyPI não fornece wheel Android oficial. Compilar no telefone é caro e pode falhar. A compatibilidade V0.1 usa temporariamente a combinação:

```text
pydantic 2.14.0b2
pydantic-core 2.49.0
```

O wheel Android é obtido do projeto público `Eutalix/android-pydantic-core` por meio de seu índice PEP 503. O bootstrap usa `--only-binary=pydantic-core`, portanto não cai silenciosamente para uma compilação Rust se o wheel não estiver disponível.

Essa solução deve ser reavaliada quando houver wheel Android adequado para uma versão estável mais nova ou quando removermos a necessidade dessa extensão nativa.

## Mirrors do Termux

Problemas de dependências de pacotes do Termux (por exemplo `openssl-tool` exigindo uma versão de `openssl` diferente da instalada) indicam repositório/mirror fora de sincronia. Isso não é corrigido pelo projeto Amora.

Como o bootstrap não depende mais de `rsync`, esse problema não bloqueia a V0.1. Para reparar o ambiente Termux, selecione um mirror saudável com `termux-change-repo` e depois faça `pkg update` / `pkg upgrade`.
